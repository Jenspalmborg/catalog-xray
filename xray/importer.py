"""Read a product export (CSV) instead of scanning the store at all.

Understands Shopify and WooCommerce exports and most spreadsheets: columns are
matched by name ("Title" or "Name", "Type" or "Category", "Tags", "Price", …).
Shopify exports put each variant on its own row; rows are grouped by handle.
"""

import csv
import io
import re

from xray.scrape import Product, clean_tags, strip_html

# Accepted column names per field, most specific first (compared lowercased, without punctuation).
COLUMNS = {
    "id": ["handle", "id", "sku", "product id", "item id", "variant sku"],
    "name": ["title", "name", "product name", "product title", "item name"],
    "category": [
        "type",
        "product type",
        "product category",
        "categories",
        "category",
        "google product category",
    ],
    "tags": ["tags", "keywords", "search keywords", "labels"],
    "price": ["variant price", "regular price", "price", "sale price"],
    "currency": ["currency"],
    "image": ["image src", "images", "image", "image url", "image link", "main image"],
    "brand": ["vendor", "brand", "manufacturer"],
    "url": ["url", "link", "permalink", "product url"],
    "description": ["body html", "description", "short description", "body"],
}


def _norm(h: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", h.lower()).strip()


def read_csv(data: bytes) -> list[Product]:
    text = data.decode("utf-8-sig", errors="replace")
    dialect = csv.Sniffer().sniff(text[:5000], delimiters=",;\t") if text.strip() else csv.excel
    rows = list(csv.DictReader(io.StringIO(text), dialect=dialect))
    if not rows:
        return []
    headers = {_norm(h): h for h in rows[0].keys() if h}
    pick = {}
    for fld, options in COLUMNS.items():
        for o in options:
            if o in headers:
                pick[fld] = headers[o]
                break
    if "name" not in pick:
        raise ValueError("No product name column found (expected a column like Title or Name).")

    grouped: dict[str, dict] = {}
    order = []
    for i, row in enumerate(rows):
        get = lambda f: (row.get(pick[f]) or "").strip() if f in pick else ""  # noqa: E731
        key = get("id") or get("name") or str(i)
        g = grouped.get(key)
        if g is None:
            g = grouped[key] = {f: get(f) for f in COLUMNS}
            order.append(key)
        else:
            # Shopify variant rows: fill in what the first row left empty.
            for f in COLUMNS:
                if not g[f] and get(f):
                    g[f] = get(f)

    out = []
    for key in order:
        g = grouped[key]
        if not g["name"]:
            continue
        tags = re.split(r"[,;|]", g["tags"]) if g["tags"] else []
        image = re.split(r"[,\s]+", g["image"])[0] if g["image"] else None
        amount = re.search(r"\d+(?:[.,]\d+)?", g["price"] or "")
        out.append(
            Product(
                id=key,
                name=g["name"],
                url=g["url"],
                brand=g["brand"] or None,
                category=(g["category"].replace(" > ", ", ") or None),
                tags=clean_tags(tags),
                price=amount.group(0).replace(",", ".") if amount else None,
                currency=g["currency"] or None,
                image=image if image and image.startswith("http") else None,
                description=strip_html(g["description"])[:600],
            )
        )
    return out
