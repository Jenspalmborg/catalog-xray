"""Turn scraped products into a BehaviorGPT catalog and embed it."""

import json
import re
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from behaviorgpt import UnboxAIClient
from behaviorgpt.resources.catalogs import CATALOG_SCHEMA

from xray.scrape import Product

NOT_PRODUCTS = re.compile(
    r"gift ?card|shipping|package protection|route protection|insurance|donation|"
    r"\bsample\b|e-?gift|warranty|test product|returns? coverage|\bcoverage\b",
    re.I,
)


def is_product(p: Product) -> bool:
    return bool(p.name) and not NOT_PRODUCTS.search(f"{p.name} {p.category or ''}")


def model_name(name: str) -> str:
    """'Men's Tree Runner - Deep Navy (White Sole)' -> 'Men's Tree Runner': colour variants are one model."""
    base = re.split(r"\s+[-–|]\s+|\s*\(", name)[0]
    return base.strip() or name


def to_parquet(products: list[Product], path: Path) -> Path:
    rows = [
        {
            "id": p.id,
            "name": p.name,
            "brand": p.brand,
            "categories": p.category,
            "image_url": p.image if p.image and p.image.startswith("https://") else None,
            "event_type": "product",
            "group": "product",
            "sales_since": None,
            "timestamp": None,
            "frequency": None,
            "market": None,
            "price": p.price,
            "currency": p.currency,
            "search_keywords": p.tags or None,
            "keywords": p.tags or None,
        }
        for p in products
    ]
    pq.write_table(pa.Table.from_pylist(rows, schema=pa.schema(CATALOG_SCHEMA)), path)
    return path


def embed(client: UnboxAIClient, parquet: Path, state: Path, log=print) -> str:
    """Upload and wait. Each API key holds one catalog: this replaces the previous one."""
    job = client.embed(parquet)
    state.write_text(json.dumps(job.model_dump(), indent=2))
    log(f"  catalog {job.catalog_id}, job {job.job_id}")
    client.catalogs.wait_for_job(job.job_id, timeout=3600, startup_grace=600, on_progress=lambda s: None)
    return job.catalog_id
