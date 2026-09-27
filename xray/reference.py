"""The reference market: BehaviorGPT's pre-embedded retail catalog.

It carries what a scraped store lacks: sales history (demand) and the model's own
product terms. Lookups are cached on disk so reruns are cheap.
"""

import ast
import hashlib
import json
import threading
from pathlib import Path

from behaviorgpt import Search, UnboxAIClient, View
from behaviorgpt._exceptions import UnboxAIError

REFERENCE = "retail_catalog"


def terms(data: dict) -> dict[str, float]:
    raw = data.get("top_terms_mapped")
    try:
        items = ast.literal_eval(raw) if isinstance(raw, str) else raw or []
    except (ValueError, SyntaxError):
        return {}
    return {t["term"]: float(t["score"]) for t in items if isinstance(t, dict) and t.get("term")}


def demand(data: dict) -> float:
    """All-time sales from sales_since, else interaction count, else 1."""
    try:
        sales = (
            ast.literal_eval(data["sales_since"])
            if isinstance(data.get("sales_since"), str)
            else data.get("sales_since")
        )
        if sales:
            return float(sales[-1]) or 1.0
    except (ValueError, SyntaxError, KeyError, TypeError):
        pass
    try:
        return float(data.get("frequency")) or 1.0
    except (TypeError, ValueError):
        return 1.0


def item_row(item) -> dict:
    d = item.data
    return {
        "id": item.id,
        "score": round(item.score, 4),
        "name": d.get("name", ""),
        "image": d.get("image_url"),
        "price": d.get("price"),
        "category": d.get("categories"),
        "terms": terms(d),
        "demand": demand(d),
    }


class Lookup:
    """Cached search / recommendation calls against any catalog."""

    def __init__(self, client: UnboxAIClient, cache: Path):
        self.client = client
        self.cache = cache
        self.cache.parent.mkdir(parents=True, exist_ok=True)
        self.memo: dict[str, list[dict]] = {}
        self.lock = threading.Lock()
        if cache.exists():
            for line in cache.open():
                k, v = json.loads(line)
                self.memo[k] = v

    def _key(self, *parts) -> str:
        return hashlib.sha1(json.dumps(parts).encode()).hexdigest()[:16]

    def _cached(self, key: str, fn) -> list[dict]:
        if key in self.memo:
            return self.memo[key]
        try:
            rows = fn()
        except (UnboxAIError, OSError):
            rows = []
        with self.lock:
            self.memo[key] = rows
            with self.cache.open("a") as f:
                f.write(json.dumps([key, rows]) + "\n")
        return rows

    def search(self, query: str, catalog: str, limit: int = 10) -> list[dict]:
        key = self._key("search", query.lower().strip(), catalog, limit)
        return self._cached(
            key,
            lambda: [
                item_row(i)
                for i in self.client.complete(
                    history=[Search(query)], catalog_id=catalog, limit=limit
                ).products.items
            ],
        )

    def after_view(self, query: str, product_id: str, catalog: str, limit: int = 10) -> list[dict]:
        """What shoppers want next after searching `query` and viewing `product_id`.
        The history ends on a view, so this is the recommendations call, not search."""
        key = self._key("after", query.lower().strip(), product_id, catalog, limit)
        return self._cached(
            key,
            lambda: [
                item_row(i)
                for i in self.client.complete(
                    history=[Search(query), View(product_id)], catalog_id=catalog, limit=limit + 1
                ).products.items
                if i.id != product_id
            ][:limit],
        )
