"""Scrape a store's products and their tags from a URL.

Tries the fastest reliable route first:
  1. Shopify     /products.json         (tags, product type, vendor)
  2. WooCommerce /wp-json/wc/store/v1   (categories, tags)
  3. Any store   sitemap -> product pages -> schema.org Product JSON-LD
Every request is checked against robots.txt and paced politely.
"""

import gzip
import html
import json
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

USER_AGENT = "catalog-xray/0.1 (+https://github.com/Jenspalmborg/catalog-xray)"
MAX_PRODUCTS = 20_000  # BehaviorGPT's catalog limit


@dataclass
class Product:
    id: str
    name: str
    url: str = ""
    brand: str | None = None
    category: str | None = None
    tags: list[str] = field(default_factory=list)
    price: str | None = None
    currency: str | None = None
    image: str | None = None
    description: str = ""


class Blocked(Exception):
    """The store refuses automated access (robots.txt or HTTP 401/403/429)."""


class Scraper:
    def __init__(self, url: str, delay: float = 1.0, log=print, page_budget: int = 150):
        self.page_budget = page_budget
        parsed = urlparse(url if "://" in url else "https://" + url)
        self.base = f"{parsed.scheme}://{parsed.netloc}"
        self.domain = parsed.netloc.removeprefix("www.")
        self.delay = delay
        self.log = log
        self.http = httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept": "application/json, text/html;q=0.9"},
            follow_redirects=True,
            timeout=30,
        )
        self.robots = RobotFileParser()
        try:
            r = self.http.get(self.base + "/robots.txt")
            self.robots.parse(r.text.splitlines() if r.status_code == 200 else [])
        except httpx.HTTPError:
            self.robots.parse([])
        self._last = 0.0

    def get(self, url: str) -> httpx.Response:
        if not self.robots.can_fetch(USER_AGENT, url):
            raise Blocked(f"robots.txt disallows {urlparse(url).path}")
        wait = self.delay - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        r = self.http.get(url)
        if r.status_code in (401, 403, 429):
            raise Blocked(f"HTTP {r.status_code} on {urlparse(url).path}")
        return r

    # ---------- Shopify ----------
    def shopify(self, limit: int) -> list[Product]:
        out, page = [], 1
        while len(out) < limit:
            r = self.get(f"{self.base}/products.json?limit=250&page={page}")
            if r.status_code != 200 or "json" not in r.headers.get("content-type", ""):
                break
            items = r.json().get("products", [])
            if not items:
                break
            for p in items:
                variant = (p.get("variants") or [{}])[0]
                images = p.get("images") or []
                tags = p.get("tags") or []
                out.append(
                    Product(
                        id=f"shopify-{p['id']}",
                        name=p.get("title", "").strip(),
                        url=f"{self.base}/products/{p.get('handle', '')}",
                        brand=p.get("vendor") or None,
                        category=p.get("product_type") or None,
                        tags=clean_tags(tags.split(",") if isinstance(tags, str) else tags),
                        price=str(variant.get("price")) if variant.get("price") is not None else None,
                        image=images[0].get("src") if images else None,
                        description=strip_html(p.get("body_html") or "")[:600],
                    )
                )
            self.log(f"  shopify page {page}: {len(out)} products")
            page += 1
        return out[:limit]

    # ---------- WooCommerce ----------
    def woocommerce(self, limit: int) -> list[Product]:
        out, page = [], 1
        while len(out) < limit:
            r = self.get(f"{self.base}/wp-json/wc/store/v1/products?per_page=100&page={page}")
            if r.status_code != 200 or not r.headers.get("content-type", "").startswith("application/json"):
                break
            items = r.json()
            if not isinstance(items, list) or not items:
                break
            for p in items:
                prices = p.get("prices") or {}
                minor = int(prices.get("currency_minor_unit") or 2)
                price = prices.get("price")
                out.append(
                    Product(
                        id=f"woo-{p['id']}",
                        name=html.unescape(p.get("name", "")).strip(),
                        url=p.get("permalink", ""),
                        category=", ".join(html.unescape(c["name"]) for c in p.get("categories", [])) or None,
                        tags=clean_tags([html.unescape(t["name"]) for t in p.get("tags", [])]),
                        price=f"{int(price) / 10**minor:.2f}" if price and str(price).isdigit() else None,
                        currency=prices.get("currency_code"),
                        image=(p.get("images") or [{}])[0].get("src"),
                        description=strip_html(p.get("short_description") or p.get("description") or "")[
                            :600
                        ],
                    )
                )
            self.log(f"  woocommerce page {page}: {len(out)} products")
            page += 1
        return out[:limit]

    # ---------- Any store: sitemap + JSON-LD ----------
    def sitemap_urls(self, limit: int) -> list[str]:
        maps = [
            line.split(":", 1)[1].strip()
            for line in self._robots_lines()
            if line.lower().startswith("sitemap:")
        ]
        maps = maps or [self.base + "/sitemap.xml", self.base + "/sitemap_index.xml"]
        seen, product_urls, all_urls = set(), [], []
        while maps and len(product_urls) < limit * 2:
            m = maps.pop(0)
            if m in seen:
                continue
            seen.add(m)
            try:
                r = self.get(m)
            except Blocked:
                continue
            if r.status_code != 200:
                continue
            try:
                root = ET.fromstring(r.content)
            except ET.ParseError:
                continue
            locs = [e.text.strip() for e in root.iter() if e.tag.endswith("loc") and e.text]
            if root.tag.endswith("sitemapindex"):
                # Product sitemaps first.
                maps = sorted(locs, key=lambda u: "product" not in u.lower()) + maps
            else:
                all_urls += locs
                product_urls += [
                    u for u in locs if re.search(r"/(products?|p|item|artikel|produkt)/", u, re.I)
                ] or ([u for u in locs] if "product" in m.lower() else [])
        if not product_urls:
            # No URL says "product": try the deepest pages first (products usually sit below
            # their categories) and let the page's own Product data decide.
            same_site = [
                u for u in dict.fromkeys(all_urls) if urlparse(u).netloc == urlparse(self.base).netloc
            ]
            product_urls = sorted(same_site, key=lambda u: -urlparse(u).path.rstrip("/").count("/"))
        return list(dict.fromkeys(product_urls))[: limit * 2]

    def _robots_lines(self) -> list[str]:
        try:
            return self.http.get(self.base + "/robots.txt").text.splitlines()
        except httpx.HTTPError:
            return []

    def jsonld(self, limit: int) -> list[Product]:
        """Last resort: read the store's own pages, one request each, capped at `page_budget`."""
        urls = self.sitemap_urls(limit)[: self.page_budget]
        self.log(f"  reading up to {len(urls)} product pages directly (1 per {self.delay:g}s)")
        out = []
        for n, u in enumerate(urls, 1):
            if len(out) >= limit:
                break
            try:
                r = self.get(u)
            except Blocked:
                continue
            if r.status_code == 200:
                p = parse_product_page(r.text, u)
                if p:
                    p.category = p.category or category_from_path(u)
                    out.append(p)
            if n % 25 == 0:
                self.log(f"  pages {n}/{len(urls)}: {len(out)} products")
        return out

    # ---------- Product feeds: one file with every product, made for machines ----------
    FEED_PATHS = [
        "/google_shopping.xml",
        "/google-shopping.xml",
        "/googleshopping.xml",
        "/feeds/google.xml",
        "/feed/google.xml",
        "/google_merchant.xml",
        "/product-feed.xml",
        "/products.xml",
        "/facebook.xml",
        "/feeds/facebook.xml",
    ]

    def feed(self, limit: int) -> list[Product]:
        for path in self.FEED_PATHS:
            try:
                r = self.get(self.base + path)
            except Blocked:
                continue
            if r.status_code != 200 or b"<" not in r.content[:200]:
                continue
            products = parse_feed(r.content, self.base)
            if products:
                self.log(f"  found a product feed at {path}")
                return products[:limit]
        return []

    # ---------- Common Crawl: copies of the store's pages, no requests to the store ----------
    def commoncrawl(self, limit: int) -> list[Product]:
        cc = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60, follow_redirects=True)
        try:
            collections = [c["id"] for c in cc.get("https://index.commoncrawl.org/collinfo.json").json()[:3]]
        except (httpx.HTTPError, ValueError):
            return []
        records = {}
        for cid in collections:
            try:
                r = cc.get(
                    f"https://index.commoncrawl.org/{cid}-index",
                    params={
                        "url": f"{self.domain}/*",
                        "output": "json",
                        "filter": ["status:200", "mime:text/html"],
                    },
                )
            except httpx.HTTPError:
                continue
            for line in r.text.splitlines():
                if line.startswith("{"):
                    rec = json.loads(line)
                    records.setdefault(rec["url"].split("?")[0].split("#")[0], rec)  # newest crawl first
        self.log(f"  Common Crawl: {len(records)} archived pages (no requests to the store)")
        if not records:
            return []
        # Deepest pages first: products usually sit below their categories.
        todo = sorted(records.items(), key=lambda kv: -urlparse(kv[0]).path.rstrip("/").count("/"))
        out, seen_ids = [], set()
        for n, (url, rec) in enumerate(todo[: max(limit * 2, 200)], 1):
            if len(out) >= limit:
                break
            try:
                off, ln = int(rec["offset"]), int(rec["length"])
                w = cc.get(
                    "https://data.commoncrawl.org/" + rec["filename"],
                    headers={"Range": f"bytes={off}-{off + ln - 1}"},
                )
                page = gzip.decompress(w.content).decode("utf-8", "replace").split("\r\n\r\n", 2)[-1]
            except (httpx.HTTPError, OSError, ValueError):
                continue
            p = parse_product_page(page, url)
            if p and p.id not in seen_ids:
                p.category = p.category or category_from_path(url)
                seen_ids.add(p.id)
                out.append(p)
            if n % 50 == 0:
                self.log(f"  archived pages {n}: {len(out)} products")
            time.sleep(0.1)  # be gentle with Common Crawl too
        return out

    def run(self, limit: int = MAX_PRODUCTS) -> tuple[str, list[Product]]:
        """Gentlest route first: store APIs and feeds take a handful of requests, Common Crawl
        none, and reading pages directly (one request each) comes last and is capped."""
        limit = min(limit, MAX_PRODUCTS)
        for name, fn in (
            ("shopify", self.shopify),
            ("woocommerce", self.woocommerce),
            ("feed", self.feed),
            ("commoncrawl", self.commoncrawl),
            ("sitemap", self.jsonld),
        ):
            try:
                products = fn(limit)
            except Blocked as exc:
                self.log(f"  {name}: blocked ({exc})")
                continue
            except (httpx.HTTPError, ValueError) as exc:
                self.log(f"  {name}: not available ({type(exc).__name__})")
                continue
            products = dedupe([p for p in products if p.name])
            if products:
                return name, products
            self.log(f"  {name}: no products found")
        raise Blocked(
            "No products found. The store may block scraping or use a layout this tool doesn't read."
        )


def parse_product_page(page: str, url: str) -> Product | None:
    """Read the schema.org Product from a page's JSON-LD, plus meta keywords."""
    for block in re.findall(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", page, re.S | re.I):
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        for node in walk(data):
            types = node.get("@type")
            if "Product" not in (types if isinstance(types, list) else [types]):
                continue
            offer = node.get("offers") or {}
            if isinstance(offer, list):
                offer = offer[0] if offer else {}
            if offer.get("@type") == "AggregateOffer":
                offer = {"price": offer.get("lowPrice"), "priceCurrency": offer.get("priceCurrency")}
            brand = node.get("brand")
            image = node.get("image")
            if isinstance(image, list):
                image = image[0] if image else None
            if isinstance(image, dict):
                image = image.get("url")
            meta_kw = re.search(r'<meta[^>]+name=["\']keywords["\'][^>]+content=["\']([^"\']+)', page, re.I)
            kw = node.get("keywords") or (meta_kw.group(1) if meta_kw else "")
            return Product(
                id=str(node.get("sku") or node.get("productID") or node.get("mpn") or url),
                name=html.unescape(str(node.get("name", ""))).strip(),
                url=url,
                brand=(brand.get("name") if isinstance(brand, dict) else brand) or None,
                category=node.get("category") if isinstance(node.get("category"), str) else None,
                tags=clean_tags(kw.split(",") if isinstance(kw, str) else kw),
                price=str(offer.get("price")) if offer.get("price") is not None else None,
                currency=offer.get("priceCurrency"),
                image=urljoin(url, image) if isinstance(image, str) else None,
                description=strip_html(str(node.get("description", "")))[:600],
            )
    return None


def parse_feed(content: bytes, base: str) -> list[Product]:
    """Google Shopping / Facebook catalog feeds (RSS or Atom with g: fields)."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return []

    def field(node, *names):
        for child in node:
            tag = child.tag.rsplit("}", 1)[-1].lower()
            if tag in names and (child.text or "").strip():
                return child.text.strip()
        return None

    out = []
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] not in ("item", "entry"):
            continue
        name = field(node, "title")
        pid = field(node, "id", "item_group_id")
        if not name or not pid:
            continue
        price = field(node, "sale_price", "price") or ""
        amount = re.search(r"[\d.,]+", price)
        currency = re.search(r"[A-Z]{3}", price)
        category = field(node, "product_type", "google_product_category")
        out.append(
            Product(
                id=str(pid),
                name=html.unescape(name),
                url=field(node, "link") or base,
                brand=field(node, "brand"),
                category=category.replace(" > ", ", ") if category else None,
                tags=clean_tags(
                    [t for t in [field(node, "custom_label_0"), field(node, "custom_label_1")] if t]
                ),
                price=amount.group(0).replace(",", ".") if amount else None,
                currency=currency.group(0) if currency else None,
                image=field(node, "image_link"),
                description=strip_html(field(node, "description") or "")[:600],
            )
        )
    return out


def category_from_path(url: str) -> str | None:
    """'/en/brushes-tools/brushes/eyeshadow-brushes/crease-brush-02' -> 'Brushes, Eyeshadow brushes'."""
    parts = [x for x in urlparse(url).path.split("/") if x][:-1]
    parts = [x for x in parts if not re.fullmatch(r"[a-z]{2}(-[a-z]{2})?", x)]  # language codes
    return ", ".join(x.replace("-", " ").capitalize() for x in parts[-2:]) or None


def walk(data):
    if isinstance(data, dict):
        yield data
        for v in data.values():
            yield from walk(v)
    elif isinstance(data, list):
        for v in data:
            yield from walk(v)


def clean_tags(tags) -> list[str]:
    """Turn store tags into plain words: 'brand::gender => womens' -> 'womens'.
    Drops ids, hashes, booleans and internal flags."""
    out = []
    for t in tags or []:
        t = str(t).strip()
        if "=>" in t:
            t = t.split("=>", 1)[1]
        elif "::" in t:
            t = t.rsplit("::", 1)[1]
        elif ":" in t and not t.startswith("http"):
            t = t.split(":", 1)[1]
        t = re.sub(r"[_-]+", " ", t).strip().lower()
        if not t or len(t) > 40 or t in {"true", "false", "undefined", "null", "none", "yes", "no"}:
            continue
        if re.fullmatch(r"[0-9a-f ]{8,}|[\d .,%]+|color [0-9a-f]{6,}.*", t):
            continue
        # Internal codes: ids with digits, tiers, price flags ("tier 4", "msrp", "oos dns", "apr26").
        if re.search(r"\d", t) or t in {"msrp", "oos", "oos dns", "dns", "sale", "new", "classic", "limited"}:
            continue
        out.append(t)
    return list(dict.fromkeys(out))


def strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def dedupe(products: list[Product]) -> list[Product]:
    seen, out = set(), []
    for p in products:
        if p.id in seen:
            continue
        seen.add(p.id)
        out.append(p)
    return out


def save(products: list[Product], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for p in products:
            f.write(json.dumps(asdict(p)) + "\n")


def load(path: Path) -> list[Product]:
    """Read a saved scrape, re-cleaning tags so older scrapes get the current rules."""
    products = [Product(**json.loads(line)) for line in path.open()]
    for p in products:
        p.tags = clean_tags(p.tags)
    return products
