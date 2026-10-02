"""The X-ray: compare a store's catalog with the market BehaviorGPT knows.

Every product is described by what it is ("women's running shoes"), because
branded names mislead search. Then:

Your market   For each kind of product you sell, the most popular market products
              of that kind, plus what shoppers go on to want after viewing them.
Assortment    Your share of models per product term vs. the market's demand share.
Gaps          Kinds of products shoppers around your range want that you don't carry.
Keywords      Words similar market products use that don't find yours today.
Search QA     Key searches run against your catalog, weak ones flagged.
"""

import re
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from xray.catalog import model_name
from xray.nouns import (
    PARENTS,
    PATTERNS,
    SYNONYMS,
    department,
    describe,
    family,
    gender,
    has_head,
    head,
    same_family,
)
from xray.reference import REFERENCE, Lookup
from xray.scrape import Product

STOP = set(
    """a an and the for with of in on to by from or at as is it its your you our new set pack pcs pc piece
pieces count ct oz fl lb inch inches cm mm size sizes small medium large xl xxl women womens woman men mens man
kids kid adult adults unisex girls boys girl boy black white blue red green pink gray grey navy color colors colour
gift gifts premium best original compatible portable home kit professional perfect heavy duty edition limited
classic ultra plus pro max mini lite light super soft comfortable comfort style stylish fashion casual all one two
three four five six made quality great high low day days use women's men's kids' girls' boys' amazon basics pair
pairs amp full free look make finish clear best new set look perfect that this under skin face beauty hair body
water very more most every person persons people show""".split()
)


@dataclass
class Model:
    name: str
    products: list[Product]
    query: str = ""
    noun: str = ""
    refs: list[dict] = field(default_factory=list)
    term: str = "other"
    terms: dict = field(default_factory=dict)
    keywords_found: list[str] = field(default_factory=list)
    keywords_missing: list[dict] = field(default_factory=list)

    @property
    def lead(self) -> Product:
        return self.products[0]

    @property
    def text(self) -> str:
        return " ".join(
            [self.name, self.lead.category or "", " ".join(t for p in self.products for t in p.tags)]
        )

    @property
    def own_words(self) -> set[str]:
        return set(words(self.text))


def words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z][a-z'-]{2,}", text.lower()) if w not in STOP]


def short(name: str, n: int = 6) -> str:
    """A searchable short form of a long product title, without the leading brand."""
    base = re.split(r",| - | – | \| |\(|\[| with | for ", name, maxsplit=1)[0]
    ws = [w for w in base.split() if not re.search(r"\d", w)] or base.split()
    return " ".join(ws[:n])


def _pattern_for(phrase: str):
    return dict(PATTERNS)[phrase]


AREA = {
    "powders": "makeup",
    "cosmetics": "makeup",
    "beauty products": "makeup",
    "beauty": "makeup",
    "sneakers": "shoes",
    "boots": "shoes",
    "sandals": "shoes",
    "slippers": "shoes",
    "footwear": "shoes",
    "clothing": "clothes",
    "apparel": "clothes",
    "sportswear": "clothes",
}

# Styles within these families answer the same need: running shoes and sneakers, crew and ankle socks.
INTERCHANGEABLE = {"footwear", "socks"}
MODIFIERS = {
    "fleece",
    "cable",
    "dress",
    "pencil",
    "tank",
    "ball",
    "cap",
    "tee",
    "sock",
    "socks",
    "boot",
    "boots",
    "shirt",
}


def stem(w: str) -> str:
    """Loose word stem for matching: running/runner -> 'runn', slip-on -> 'slip'."""
    w = re.sub(r"[^a-z]", "", w.lower())
    return w[: max(4, len(w) - 3)]


TYPE_STOP = {
    "self",
    "album",
    "photo",
    "book",
    "books",
    "fit",
    "style",
    "waist",
    "sleeve",
    "neck",
    "cut",
    "leg",
    "look",
    "wear",
    "everyday",
}


def types_of(noun: str, names: list[str], n: int = 4) -> list[str]:
    """The styles shoppers pick within a kind: "High Waist Baggy Cargo Jeans" -> high waist, baggy, cargo.
    Counted across brands, so a brand or product line ("Eversoft") isn't mistaken for a style,
    and product nouns ("shorts") are left out: a type describes, it doesn't name."""
    kind_words = {stem(w) for w in noun.split()}
    brands = defaultdict(set)
    for name in names:
        raw = re.findall(r"[a-z][a-z'-]+", short(name, 10).lower())
        brand, ws = (raw[0] if raw else ""), raw[1:]
        ws = [w for w in ws if w not in STOP and stem(w) not in kind_words and len(w) > 2]
        for g in set(ws) | {f"{a} {b}" for a, b in zip(ws, ws[1:])}:
            # A type describes; it isn't itself a product noun, and has no noun inside it either.
            if any(w in TYPE_STOP for w in g.split()) or any(
                pat.fullmatch(w) for w in g.split() for _, pat in PATTERNS
            ):
                continue
            brands[g].add(brand)
    ranked = sorted(brands, key=lambda g: (-len(brands[g]), -len(g.split()), g))
    picked = []
    for g in ranked:
        if len(brands[g]) < 3:  # a style is something several brands make, not one team or product line
            break
        if not any(g in p or p in g for p in picked):
            picked.append(g)
        if len(picked) == n:
            break
    return picked


def kind(name: str) -> str:
    """The product noun of a market product: the earliest (then longest) match in its title's
    first part, so "Men's Casual Dress Shoes, Oxford" -> "dress shoes", not "dress"."""
    base = re.split(r",| - | – | \| |\(|\[| with | for ", name, maxsplit=1)[0]
    found = []
    for phrase, pat in PATTERNS:
        mt = pat.search(base)
        if mt:
            found.append((mt.start(), -len(phrase), phrase))
    found.sort()
    # "Cable Knit Cardigan", "Dress Pants", "Pencil Skirt": a modifier, when another noun follows.
    while len(found) > 1 and found[0][2] in MODIFIERS:
        found.pop(0)
    return found[0][2] if found else ""


def run(
    products: list[Product],
    own: str,
    look: Lookup,
    log=print,
    max_models: int = 400,
    keyword_models: int = 250,
    queries: list[str] | None = None,
    workers: int = 8,
) -> dict:
    pool = ThreadPoolExecutor(max_workers=workers)
    by_id = {p.id: p for p in products}

    # ---- Models: colour/size variants count once; describe each by what it is ----
    groups: dict[str, list[Product]] = defaultdict(list)
    for p in products:
        groups[model_name(p.name)].append(p)
    models = sorted((Model(k, v) for k, v in groups.items()), key=lambda m: -len(m.products))[:max_models]
    for m in models:
        m.query, m.noun = describe(m.lead.name, m.lead.category, m.lead.tags, m.lead.description)
    # A category like "Shoes" says little. Ask your own catalog which specific kind each product is:
    # search it for "running shoes", "sneakers", "slip ons"… and take the best-ranked kind per product.
    # Only vague kinds ("shoes", "makeup") get refined; a stated kind ("highlighter") stays as it is.
    generic = [m for m in models if m.noun in PARENTS]
    options = sorted({p for m in generic for p in same_family(m.noun)} | {m.noun for m in generic})
    ranks = dict(
        zip(options, pool.map(lambda q: {h["id"]: i for i, h in enumerate(look.search(q, own, 60))}, options))
    )
    for m in generic:
        ids = [p.id for p in m.products]
        scored = []
        for o in same_family(m.noun):
            if o not in ranks:
                continue
            rank = min((ranks[o].get(i, 99) for i in ids), default=99)
            # "running shoes" needs run/runner in the product's own text; a top-3 rank alone also counts.
            words_ = o.split()[:-1]
            # Evidence: the modifier in the name or tags ("Runner" -> running shoes), or the whole
            # phrase in the description. A stray word in the description ("works") is not enough.
            in_name = bool(words_) and all(re.search(rf"\b{stem(w)}", m.text.lower()) for w in words_)
            in_desc = _pattern_for(o).search(m.lead.description) is not None
            if in_name or (in_desc and rank < 40):
                scored.append((rank, -len(o.split()), o))
        best = min(scored) if scored else None
        if best and best[2] != m.noun:
            m.noun = best[2]
            m.query = f"{gender(m.name)} {m.noun}".strip()
    fam_of = {p.id: family(m.noun) for m in models for p in m.products if m.noun}
    depts = Counter(department(m.noun) for m in models if m.noun)
    your_departments = {d for d, c in depts.items() if d and c >= 0.05 * len(models)}
    heads_you_sell = {head(m.noun) for m in models if m.noun}
    nouns_you_sell = {m.noun for m in models if m.noun}
    your_families = set(fam_of.values())
    log(
        f"  {len(products)} products -> {len(models)} models, "
        f"{len({m.noun for m in models if m.noun})} kinds in {len(your_families)} families"
    )

    # ---- Your market: popular market products of the same kind, plus what shoppers want next ----
    GENERIC = {
        "footwear": "shoes",
        "socks": "socks",
        "tops": "shirts",
        "bottoms": "pants",
        "outerwear": "jackets",
        "underwear": "underwear",
        "sleepwear": "pajamas",
        "headwear": "hats",
        "bags": "bags",
    }

    def market_for(m: Model) -> list[dict]:
        """Popular market products of the same family as this model, same kind first."""
        if not m.noun:
            return []
        fam = family(m.noun)
        same = []
        for q in dict.fromkeys([m.noun, GENERIC.get(fam, m.noun)]):
            hits = look.search(q, REFERENCE, 30)
            same = [r for r in hits if family(kind(r["name"]) or "") == fam]
            if len(same) >= 3:
                break
        same.sort(key=lambda r: not has_head(r["name"], head(m.noun)))
        g = gender(m.name)
        same.sort(key=lambda r: (g and gender(r["name"]) != g, -r["score"]))
        return same[:20]

    refs = list(pool.map(market_for, models))
    nexts = list(
        pool.map(
            lambda mr: (
                look.after_view(mr[0].noun or short(mr[0].name), mr[1][0]["id"], REFERENCE, 10)
                if mr[1]
                else []
            ),
            zip(models, refs),
        )
    )
    # Your own areas: the model's top terms for the market products closest to each of yours.
    area_count = Counter(
        t for near in refs if near for t in list(max(near[:3], key=lambda r: r["score"])["terms"])[:1]
    )
    your_areas = {t for t, c in area_count.items() if c >= 0.08 * len(models)}
    your_areas |= {AREA.get(t, t) for t in your_areas}
    tops = Counter(
        max(r["terms"], key=r["terms"].get)
        for m, near in zip(models, refs)
        if m.noun
        for r in near
        if r["terms"]
    )
    domain = {t for t, c in tops.items() if c >= 0.06 * sum(tops.values())}
    market: dict[str, dict] = {}
    for m, near, after in zip(models, refs, nexts):
        after = [
            r for r in after if r["terms"] and max(r["terms"], key=r["terms"].get) in domain & your_areas
        ]
        m.refs = near
        weights = Counter()
        for rank, r in enumerate(near[:5]):
            for t, s in r["terms"].items():
                weights[t] += s / (rank + 1)
        m.terms = dict(weights.most_common(3))
        m.term = next(iter(m.terms), "other")
        for r in near:
            market.setdefault(r["id"], {**r, "via": set(), "kind": "similar"})["via"].add(m.name)
        for r in after:
            market.setdefault(r["id"], {**r, "via": set(), "kind": "next"})["via"].add(m.name)
    log(f"  market: {len(market)} reference products around your catalog")

    # ---- Assortment balance by the model's product terms ----
    # The model has separate terms for close kinds ("shoes", "sneakers"); count them as one area.
    for m in models:
        m.term = AREA.get(m.term, m.term)
    for r in market.values():
        r["terms"] = {AREA.get(t, t): v for t, v in r["terms"].items()}
    yours = Counter(m.term for m in models)
    demand = Counter()
    for r in market.values():
        if department(kind(r["name"])) not in your_departments:
            continue  # only products of the departments you sell in count towards the balance
        top = max(r["terms"], key=r["terms"].get) if r["terms"] else "other"
        demand[top] += r["demand"]
    total_models, total_demand = sum(yours.values()), sum(demand.values()) or 1
    balance = []
    for t in set(yours) | set(demand):
        if t == "other":
            continue
        ys, ms = yours[t] / total_models, demand[t] / total_demand
        balance.append(
            {
                "term": t,
                "your_models": yours[t],
                "your_share": ys,
                "market_share": ms,
                "index": (ys / ms) if ms else None,
            }
        )
    balance.sort(key=lambda b: -(b["market_share"] + b["your_share"]))

    # ---- Gaps: kinds of products shoppers around you want that you don't carry ----
    def carried(noun: str) -> tuple[bool, str | None]:
        """Does your catalog have this kind of product? Search it and check the top results."""
        if (
            head(noun) in heads_you_sell
            or SYNONYMS.get(noun) in nouns_you_sell
            or noun in {SYNONYMS.get(n) for n in nouns_you_sell}
            or (family(noun) in INTERCHANGEABLE and family(noun) in your_families)
        ):
            return True, None
        hits = look.search(noun, own, 5)
        for hit in hits:
            p = by_id.get(hit["id"])
            if p and has_head(" ".join([p.name, p.category or "", " ".join(p.tags)]), head(noun)):
                return True, p.name
        return False, hits[0]["name"] if hits else None

    # Gaps must sit in an area you actually sell in (by the model's own terms). Shoppers of eyeliner
    # also buy pens, but pens aren't part of a beauty brand's assortment.
    kinds = defaultdict(list)
    for r in market.values():
        k = kind(r["name"])
        if k and department(k) in your_departments:
            kinds[k].append(r)
    checked = dict(zip(kinds, pool.map(carried, kinds)))
    gaps = []
    for k, rs in kinds.items():
        have, closest = checked[k]
        if have:
            continue
        # A gap seen only in what shoppers look at next needs backing from at least two of your products.
        if all(r["kind"] == "next" for r in rs) and len({v for r in rs for v in r["via"]}) < 2:
            continue
        rs.sort(key=lambda r: -r["demand"])
        via = Counter(v for r in rs for v in r["via"])
        prices = sorted(
            float(r["price"]) for r in rs if re.fullmatch(r"\d+(\.\d+)?", str(r.get("price") or ""))
        )
        gaps.append(
            {
                "kind": k,
                "demand": sum(r["demand"] for r in rs),
                "count": len(rs),
                "share_next": sum(r["kind"] == "next" for r in rs) / len(rs),
                "via": [v for v, _ in via.most_common(3)],
                "closest": closest,
                "types": types_of(k, [r["name"] for r in rs]),
                "price_low": prices[max(0, len(prices) // 10)] if prices else None,
                "price_high": prices[min(len(prices) - 1, len(prices) * 9 // 10)] if prices else None,
                "examples": [
                    {
                        "name": r["name"],
                        "query": short(r["name"]),
                        "image": r["image"],
                        "price": r["price"],
                        "demand": r["demand"],
                    }
                    for r in rs[:6]
                ],
            }
        )
    gaps.sort(key=lambda g: -g["demand"])

    # Describe each gap from a wider sample of that kind in the market: its styles and price range.
    def profile(g):
        sample = [x for x in look.search(g["kind"], REFERENCE, 30) if has_head(x["name"], head(g["kind"]))]
        names = [x["name"] for x in sample] + [x["name"] for x in g["examples"]]
        g["types"] = types_of(g["kind"], names)
        prices = sorted(
            float(x["price"])
            for x in sample + g["examples"]
            if re.fullmatch(r"\d+(\.\d+)?", str(x.get("price") or "")) and float(x["price"]) > 0
        )
        if prices:
            g["price_low"], g["price_high"] = (
                prices[len(prices) // 10],
                prices[(len(prices) * 9) // 10 - (len(prices) % 10 == 0)],
            )

    list(pool.map(profile, gaps[:15]))
    log(f"  departments you sell in: {', '.join(sorted(your_departments)) or 'none recognised'}")
    log(f"  gaps: {len(gaps)} kinds of product around your range that you don't carry")

    # ---- Keywords per model ----
    # Shopper vocabulary: words and two-word phrases used by at least 3 brands across your market.
    vocab_b = defaultdict(set)
    for r in market.values():
        first = r["name"].split()[0].lower() if r["name"] else ""
        ws = [w for w in re.findall(r"[a-z][a-z'-]+", short(r["name"], 12).lower()) if w != first]
        for w in ws:
            if w not in STOP and len(w) >= 4:
                vocab_b[w].add(first)
        for a, b in zip(ws, ws[1:]):
            if a not in STOP and b not in STOP:
                vocab_b[f"{a} {b}"].add(first)
    vocab = {w for w, brands in vocab_b.items() if len(brands) >= 3}
    vocab_rx = {w: re.compile(rf"\b{re.escape(w)}\b", re.I) for w in vocab}

    def keywords(m: Model):
        own_text = m.text.lower()  # name, category and tags: what search matches on
        known = lambda w: all(re.search(rf"\b{stem(x)}", own_text) for x in w.split())  # noqa: E731
        # 1. Words most of this product's market neighbours use, across different brands.
        near = Counter()
        for w in vocab:
            n = sum(1 for r in m.refs if vocab_rx[w].search(r["name"]))
            if n >= 3:
                near[w] = n

        def other_kind(w: str) -> bool:
            """A word that names a different kind of product isn't a keyword for this one."""
            return any(pat.fullmatch(w) and family(ph) != family(m.noun or "") for ph, pat in PATTERNS)

        cands = {
            w: f"used by {n} similar market products"
            for w, n in near.most_common(8)
            if not known(w) and not other_kind(w)
        }
        # 2. Shopper words your own description uses but your name and tags don't.
        for w in sorted(vocab, key=lambda w: (-len(w.split()), w)):
            if len(cands) >= 6:
                break
            if (
                w not in cands
                and not known(w)
                and not other_kind(w)
                and vocab_rx[w].search(m.lead.description or "")
            ):
                cands[w] = "in your description, not in your tags"
        # One form per idea: keep "slip-on" over "slip", "loafers" over "loafer".
        keep = {}
        for w, why in sorted(cands.items(), key=lambda kv: -len(kv[0])):
            key = " ".join(stem(x) for x in re.split(r"[\s-]+", w))
            if not any(key in k or k in key for k in keep):
                keep[key] = (w, why)
        cands = dict(list(keep.values())[:5])
        ids = {p.id for p in m.products}
        for w, why in cands.items():
            q = w if (not m.noun or head(m.noun) in w) else f"{w} {m.noun}"
            hits = look.search(q, own, 20)
            if any(h["id"] in ids for h in hits):
                m.keywords_found.append(w)
            else:
                m.keywords_missing.append({"keyword": w, "why": why, "query": q})

    list(pool.map(keywords, models[:keyword_models]))
    log(
        f"  keywords: {sum(len(m.keywords_missing) for m in models)} to add across {keyword_models} models "
        f"(shopper vocabulary: {len(vocab)} terms)"
    )

    # ---- Search QA ----
    auto = [n for n, _ in Counter(m.noun for m in models if m.noun).most_common(14)]
    missing_kw = Counter(k["keyword"] for m in models for k in m.keywords_missing)
    auto += [w for w, _ in missing_kw.most_common(12)]
    qs = list(dict.fromkeys((queries or []) + auto))[:40]

    def qa(q):
        own_hits = look.search(q, own, 5)
        k = describe(q, None, [], "")[1]
        if k:
            accept = PARENTS.get(k, set()) | {family(k)}
            relevant = [x for x in own_hits if fam_of.get(x["id"]) in accept]
            # Weak = the search misses products you actually have; a store with one print can't show three.
            have = sum(1 for f in fam_of.values() if f in accept)
            weak = len(relevant) < min(3, have)
            note = f"{len(relevant)} of top 5 are {family(k) if family(k) != head(k) else k}"
        else:
            # No product noun in the query: judge by whether the top results carry the word.
            relevant = [
                x
                for x in own_hits
                if (p := by_id.get(x["id"]))
                and all(
                    stem(w) in " ".join([p.name, p.category or "", " ".join(p.tags), p.description]).lower()
                    for w in q.split()
                )
            ]
            weak = len(relevant) < 2
            note = f"{len(relevant)} of top 5 mention “{q}”"
        return {"query": q, "own": own_hits, "weak": weak, "note": note, "custom": q in (queries or [])}

    qa_rows = list(pool.map(qa, qs))
    pool.shutdown()

    return {
        "products": len(products),
        "models": [
            {
                "name": m.name,
                "variants": len(m.products),
                "query": m.query,
                "term": m.term,
                "terms": m.terms,
                "image": m.lead.image,
                "url": m.lead.url,
                "price": m.lead.price,
                "category": m.lead.category,
                "tags": m.lead.tags[:12],
                "found": m.keywords_found,
                "missing": m.keywords_missing,
                "ids": [p.id for p in m.products],
            }
            for m in models
        ],
        "balance": balance,
        "gaps": gaps,
        "queries": qa_rows,
        "market_size": len(market),
    }
