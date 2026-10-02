"""One scan: scrape, upload, analyse, report. Shared by the command line and the web app.

Each step is cached under out/<domain>/, so a rerun only redoes what changed.
"""

import csv
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Callable

from xray import analyze, catalog, report
from xray.reference import Lookup
from xray.scrape import Blocked, Scraper, load, save

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
STATE = Path.home() / ".cache" / "catalog-xray" / "uploaded.json"
SOURCES = {
    "shopify": "Shopify's product feed (a few requests)",
    "woocommerce": "the WooCommerce store API (a few requests)",
    "feed": "the store's product feed (one request)",
    "commoncrawl": "Common Crawl's public web archive (no requests to the store)",
    "sitemap": "a sample of the store's product pages",
    "csv": "an uploaded product export (no requests to the store)",
}
STEPS = ["Read products", "Upload", "Analyse", "Report"]


class ScanError(Exception):
    """A scan that can't go on, with a message meant for the person running it."""


def client():
    from behaviorgpt import UnboxAIClient
    from behaviorgpt._exceptions import AuthenticationError
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    try:
        return UnboxAIClient(market="us")
    except AuthenticationError:
        raise ScanError("No UNBOXAI_API_KEY. Put it in .env (see .env.example).") from None


def domain_of(url: str) -> str:
    from urllib.parse import urlparse

    return urlparse(url if "://" in url else "https://" + url).netloc.removeprefix("www.")


def scan(
    url: str,
    max_products: int = 5000,
    fresh: bool = False,
    queries: list[str] | None = None,
    delay: float = 1.0,
    out_root: Path = OUT,
    page_budget: int = 150,
    step: Callable[[int, str], None] = lambda i, msg: print(f"{i + 1}/4 {msg}"),
    log: Callable[[str], None] = lambda msg: print("    " + msg),
) -> Path:
    """Run a whole scan and return the report path. `step(i, message)` marks progress through STEPS."""
    api = client()
    t0 = time.monotonic()
    scraper = Scraper(url, delay=delay, log=log, page_budget=page_budget)
    out = out_root / scraper.domain
    out.mkdir(parents=True, exist_ok=True)

    # 1. Scrape
    raw, meta = out / "products.jsonl", out / "scrape.json"
    if raw.exists() and meta.exists() and not fresh:
        products, source = load(raw), json.loads(meta.read_text())["source"]
        step(0, f"Using the earlier scrape of {scraper.domain}: {len(products)} products.")
    else:
        step(0, f"Reading products from {scraper.base}, gentlest route first …")
        try:
            source, products = scraper.run(limit=max_products)
        except Blocked as exc:
            raise ScanError(f"Couldn’t scrape {scraper.base}: {exc}") from None
        save(products, raw)
        meta.write_text(json.dumps({"source": source, "url": scraper.base, "count": len(products)}))
        log(f"{len(products)} products via {SOURCES[source]}")
    products = [p for p in products if catalog.is_product(p)]
    if len(products) < 5:
        raise ScanError(f"Only {len(products)} products found on {scraper.domain}; too few to analyse.")
    if len(products) < 20:
        log(f"Only {len(products)} products: a small range, so treat the results as indicative.")

    return _finish(api, products, scraper.domain, source, out, queries, step, log, t0)


def scan_file(
    data: bytes,
    name: str,
    queries: list[str] | None = None,
    out_root: Path = OUT,
    step: Callable[[int, str], None] = lambda i, msg: print(f"{i + 1}/4 {msg}"),
    log: Callable[[str], None] = lambda msg: print("    " + msg),
) -> Path:
    """Analyse a product export (CSV): nothing is fetched from the store."""
    from xray.importer import read_csv

    api = client()
    t0 = time.monotonic()
    slug = re.sub(r"[^a-z0-9.-]+", "-", name.lower()).strip("-") or "upload"
    out = out_root / slug
    out.mkdir(parents=True, exist_ok=True)
    step(0, f"Reading the export for {slug} …")
    try:
        products = read_csv(data)
    except (ValueError, UnicodeDecodeError, csv.Error) as exc:
        raise ScanError(f"Couldn’t read that file: {exc}") from None
    save(products, out / "products.jsonl")
    (out / "scrape.json").write_text(json.dumps({"source": "csv", "url": "", "count": len(products)}))
    log(f"{len(products)} products in the file")
    products = [p for p in products if catalog.is_product(p)]
    if len(products) < 20:
        raise ScanError(f"Only {len(products)} products in the file; too few to analyse.")
    return _finish(api, products, slug, "csv", out, queries, step, log, t0)


def _finish(api, products, domain, source, out, queries, step, log, t0) -> Path:
    # 2. Upload (one catalog per API key: re-upload if another store is loaded)
    digest = hashlib.sha1(
        "".join(sorted(p.id + p.name + ",".join(p.tags) for p in products)).encode()
    ).hexdigest()[:12]
    STATE.parent.mkdir(parents=True, exist_ok=True)
    current = json.loads(STATE.read_text()) if STATE.exists() else {}
    if current.get("digest") == digest:
        cid = current["catalog_id"]
        step(1, f"{domain} is already uploaded.")
    else:
        step(1, f"Uploading {len(products)} products to BehaviorGPT. This takes a few minutes …")
        cid = catalog.embed(
            api, catalog.to_parquet(products, out / "catalog.parquet"), out / "catalog.json", log=log
        )
        STATE.write_text(json.dumps({"digest": digest, "catalog_id": cid, "domain": domain}))

    # 3. Analyse
    step(2, "Comparing your catalog with the market …")
    look = Lookup(api, out / f"lookups-{digest}.jsonl")
    result = analyze.run(products, cid, look, log=log, queries=queries)
    (out / "result.json").write_text(json.dumps(result, default=list))

    # 4. Report
    path = report.write(result, domain, SOURCES[source], out)
    step(3, f"Report ready in {time.monotonic() - t0:.0f}s.")
    return path
