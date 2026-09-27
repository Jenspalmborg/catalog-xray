"""Write the X-ray as one self-contained HTML page plus CSV exports."""

import csv
import html
import re
from datetime import date
from pathlib import Path

e = html.escape


def pct(x: float) -> str:
    return f"{x * 100:.0f}%" if x >= 0.01 or x == 0 else "<1%"


def write_csvs(result: dict, out: Path) -> dict[str, str]:
    files = {}
    with (out / "keywords.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["product", "variants", "product_ids", "current_tags", "add_keywords", "already_found_by"])
        for m in result["models"]:
            if m["missing"] or m["found"]:
                w.writerow(
                    [
                        m["name"],
                        m["variants"],
                        " ".join(m["ids"]),
                        ", ".join(m["tags"]),
                        ", ".join(k["keyword"] for k in m["missing"]),
                        ", ".join(m["found"]),
                    ]
                )
    files["keywords"] = "keywords.csv"
    with (out / "assortment_gaps.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "kind_to_add",
                "types",
                "price_range_usd",
                "market_products",
                "market_popularity",
                "shoppers_of_yours",
                "example_products",
            ]
        )
        for g in result["gaps"]:
            w.writerow(
                [
                    g["kind"],
                    ", ".join(g.get("types", [])),
                    price_range(g),
                    g["count"],
                    int(g["demand"]),
                    "; ".join(g["via"]),
                    " | ".join(x["name"] for x in g["examples"][:3]),
                ]
            )
    files["gaps"] = "assortment_gaps.csv"
    with (out / "search_qa.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["query", "status", "why", "your_top_results"])
        for q in result["queries"]:
            w.writerow(
                [
                    q["query"],
                    "weak" if q["weak"] else "ok",
                    q["note"],
                    " | ".join(h["name"] for h in q["own"][:5]),
                ]
            )
    files["qa"] = "search_qa.csv"
    return files


def balance_chart(balance: list[dict]) -> str:
    rows = [b for b in balance if b["market_share"] >= 0.03 or b["your_share"] >= 0.03][:10]
    top = max([max(b["your_share"], b["market_share"]) for b in rows] or [1])
    out = []
    for b in rows:
        idx = b["index"]
        if b["your_models"] == 0:
            tag = '<span class="flag under">Missing</span>'
        elif idx is not None and idx < 0.6:
            tag = '<span class="flag under">Under-stocked</span>'
        elif idx is not None and idx > 1.8:
            tag = '<span class="flag over">Over-weighted</span>'
        else:
            tag = '<span class="flag ok">Balanced</span>'
        out.append(f"""<div class="brow">
  <div class="bl">{e(b["term"].capitalize())}{tag}</div>
  <div class="bars">
    <div class="bar you" style="width:{b["your_share"] / top * 100:.1f}%" data-tip="Your catalog: {pct(b["your_share"])} of models ({b["your_models"]})"></div><span class="bv">{pct(b["your_share"])}</span>
    <div class="bar mkt" style="width:{b["market_share"] / top * 100:.1f}%" data-tip="Market demand: {pct(b["market_share"])} of sales"></div><span class="bv">{pct(b["market_share"])}</span>
  </div>
</div>""")
    table = "".join(
        f"<tr><td>{e(b['term'])}</td><td>{b['your_models']}</td><td>{pct(b['your_share'])}</td>"
        f"<td>{pct(b['market_share'])}</td></tr>"
        for b in rows
    )
    return (
        "\n".join(out)
        + f"""<details class="tbl"><summary>Show as table</summary><table>
<tr><th>Area</th><th>Your models</th><th>Your share</th><th>Market demand</th></tr>{table}</table></details>"""
    )


def trim(name: str, words: int = 6) -> str:
    """Long marketplace titles, cut to something readable: first part, a few words."""
    base = re.split(r",| - | – | \| |\(", name, maxsplit=1)[0].strip()
    ws = base.split()
    return " ".join(ws[:words]) + ("…" if len(ws) > words else "")


def price_range(g: dict) -> str:
    lo, hi = g.get("price_low"), g.get("price_high")
    if lo is None:
        return "—"
    return f"${lo:,.0f}" if round(lo) == round(hi) else f"${lo:,.0f}–{hi:,.0f}"


def gap_table(gaps: list[dict]) -> str:
    if not gaps:
        return (
            '<p class="empty">No clear gaps: you carry every kind of product popular around your range.</p>'
        )
    rows = []
    for g in gaps[:12]:
        via = ", ".join(e(trim(v)) for v in g["via"][:2])
        how = "look at these next" if g["share_next"] >= 0.5 else "also browse these"
        types = ", ".join(e(t) for t in g.get("types", [])) or "—"
        rows.append(
            f"<tr><td class='gk'>{e(g['kind'].capitalize())}</td><td>{types}</td>"
            f"<td class='gp'>{price_range(g)}</td>"
            f"<td class='gw'>Shoppers of your <b>{via}</b> {how}<small>{g['count']} popular product{'s' if g['count'] != 1 else ''} in the market</small></td></tr>"
        )
    return (
        '<div class="card scroll"><table class="gaps"><tr><th>Add</th><th>Types shoppers pick</th><th>Market price</th>'
        "<th>Why</th></tr>" + "".join(rows) + "</table></div>"
    )


def keyword_rows(models: list[dict]) -> str:
    rows = []
    for m in models:
        if not (m["missing"] or m["found"]):
            continue
        miss = (
            "".join(
                f'<span class="chip add" title="{e(k["why"])}; searching “{e(k["query"])}” doesn’t find it">+ {e(k["keyword"])}</span>'
                for k in m["missing"]
            )
            or '<span class="none">none</span>'
        )
        found = "".join(f'<span class="chip ok">{e(w)}</span>' for w in m["found"])
        tags = ", ".join(e(t) for t in m["tags"][:8]) or "—"
        rows.append(f'''<tr data-q="{e((m["name"] + " " + " ".join(k["keyword"] for k in m["missing"])).lower())}">
  <td class="kp">{f'<img src="{e(m["image"])}" alt="" loading="lazy">' if m.get("image") else ""}<div><a href="{e(m["url"])}" target="_blank" rel="noopener">{e(m["name"])}</a><small>{m["variants"]} variant{"s" if m["variants"] != 1 else ""} · {e(m["term"])}</small></div></td>
  <td class="tags">{tags}</td>
  <td>{miss}</td>
  <td>{found}</td>
</tr>''')
    return "\n".join(rows)


def qa_rows(queries: list[dict]) -> str:
    out = []
    for q in sorted(queries, key=lambda q: (not q["weak"], q["query"])):

        def hit(h):
            img = f'<img src="{e(h["image"])}" alt="" loading="lazy">' if h.get("image") else ""
            return f'<span class="qhit" title="{e(h["name"])}">{img}<span>{e(h["name"][:38])}</span></span>'

        thumbs = "".join(hit(h) for h in q["own"][:3])
        status = '<span class="flag under">Weak</span>' if q["weak"] else '<span class="flag ok">Good</span>'
        out.append(
            f"<tr><td><b>{e(q['query'])}</b>{' <small>yours</small>' if q['custom'] else ''}</td>"
            f'<td>{status}<div class="qnote">{e(q["note"])}</div></td>'
            f'<td class="qhits">{thumbs or "<span class=none>no results</span>"}</td></tr>'
        )
    return "\n".join(out)


def write(result: dict, domain: str, source: str, out: Path) -> Path:
    files = write_csvs(result, out)
    models = result["models"]
    n_missing = sum(1 for m in models if m["missing"])
    n_kw = sum(len(m["missing"]) for m in models)
    weak = [q for q in result["queries"] if q["weak"]]
    top_gap = result["gaps"][0] if result["gaps"] else None

    page = TEMPLATE
    for key, value in {
        "DOMAIN": e(domain),
        "DATE": date.today().isoformat(),
        "SOURCE": e(source),
        "N_PRODUCTS": f"{result['products']:,}",
        "N_MODELS": f"{len(models):,}",
        "MARKET": f"{result['market_size']:,}",
        "N_GAPS": str(len(result["gaps"])),
        "N_MISSING_MODELS": str(n_missing),
        "N_KW": str(n_kw),
        "N_WEAK": str(len(weak)),
        "N_QUERIES": str(len(result["queries"])),
        "HEADLINE": (
            f"Biggest opportunity: <b>{e(top_gap['kind'])}</b>. Shoppers of your "
            f"{e(trim(top_gap['via'][0])) if top_gap['via'] else 'products'} go for {top_gap['count']} popular "
            f"{e(top_gap['kind'])} in the market, and you don’t carry any."
        )
        if top_gap
        else "You carry every kind of product that’s popular around your range.",
        "BALANCE": balance_chart(result["balance"]),
        "GAPS": gap_table(result["gaps"]),
        "KEYWORDS": keyword_rows(models),
        "QA": qa_rows(result["queries"]),
        "CSV_KW": files["keywords"],
        "CSV_GAPS": files["gaps"],
        "CSV_QA": files["qa"],
    }.items():
        page = page.replace("{{" + key + "}}", value)
    path = out / "index.html"
    path.write_text(page)
    return path


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Catalog X-ray: {{DOMAIN}}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,700;12..96,800&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #fbfbfa; --surface: #fff; --line: rgba(22,22,40,.09); --ink: #17171f; --ink-2: #565a6b; --ink-3: #8d909e;
    --you: #2a78d6; --mkt: #eb6834; --good: #1f8a4c; --bad: #c9362b; --warn: #9a6a00;
    --display: "Bricolage Grotesque", Inter, sans-serif; --sans: Inter, -apple-system, "Segoe UI", sans-serif;
    color-scheme: light;
  }
  @media (prefers-color-scheme: dark) {
    :root { --bg: #141413; --surface: #1c1c1b; --line: rgba(255,255,255,.1); --ink: #f3f2ee; --ink-2: #c3c2b7; --ink-3: #8d8c84;
            --you: #3987e5; --mkt: #d95926; --good: #3fb26d; --bad: #e5594d; --warn: #d9a520; color-scheme: dark; }
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--ink); font: 15px/1.55 var(--sans); -webkit-font-smoothing: antialiased; }
  .wrap { max-width: 1100px; margin: 0 auto; padding: 0 24px; }
  .topbar { border-bottom: 1px solid var(--line); }
  .topbar .wrap { display: flex; justify-content: space-between; align-items: center; height: 56px; }
  .brand { display: flex; align-items: center; gap: 9px; font-family: var(--display); font-weight: 800; font-size: 17px; }
  .logo { width: 24px; height: 24px; border-radius: 7px; background: var(--you); display: grid; place-items: center; }
  .logo svg { width: 14px; height: 14px; }
  #back { color: var(--ink-2); text-decoration: none; font-size: 14px; font-weight: 500; }
  #back:hover { color: var(--ink); }
  header { padding: 36px 0 8px; }
  .kicker { font-size: 12px; font-weight: 600; letter-spacing: .1em; text-transform: uppercase; color: var(--ink-3); }
  h1 { font-family: var(--display); font-weight: 800; font-size: clamp(32px, 5vw, 48px); letter-spacing: -.03em; line-height: 1.05; margin: 6px 0 10px; }
  .lede { color: var(--ink-2); max-width: 70ch; margin: 0; }
  .headline { margin: 20px 0 0; padding: 14px 18px; border-left: 4px solid var(--you); background: var(--surface); border-radius: 10px; }
  .stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 24px 0 8px; }
  .stat { background: var(--surface); border: 1px solid var(--line); border-radius: 14px; padding: 16px 18px; }
  .stat b { display: block; font-family: var(--display); font-size: 34px; font-weight: 800; letter-spacing: -.02em; line-height: 1.1; }
  .stat span { color: var(--ink-2); font-size: 13.5px; }
  nav { position: sticky; top: 0; background: color-mix(in srgb, var(--bg) 90%, transparent); backdrop-filter: blur(8px); z-index: 5;
        border-bottom: 1px solid var(--line); margin-top: 24px; }
  nav .wrap { display: flex; gap: 22px; overflow-x: auto; }
  nav a { color: var(--ink-2); text-decoration: none; font-weight: 500; font-size: 14px; padding: 12px 0; white-space: nowrap; }
  nav a:hover { color: var(--ink); }
  section.block { padding: 40px 0 8px; }
  h2 { font-family: var(--display); font-weight: 800; font-size: 28px; letter-spacing: -.02em; margin: 0 0 6px; }
  .sub { color: var(--ink-2); margin: 0 0 20px; max-width: 72ch; }
  .card { background: var(--surface); border: 1px solid var(--line); border-radius: 16px; padding: 20px 22px; }
  .dl { display: inline-flex; gap: 6px; align-items: center; font-size: 13px; font-weight: 600; color: var(--you); text-decoration: none; margin-top: 12px; }

  /* Balance chart */
  .legend { display: flex; gap: 18px; font-size: 13px; color: var(--ink-2); margin-bottom: 14px; }
  .legend i { display: inline-block; width: 10px; height: 10px; border-radius: 3px; margin-right: 6px; vertical-align: -1px; }
  .brow { display: grid; grid-template-columns: 240px 1fr; gap: 16px; align-items: center; padding: 8px 0; border-top: 1px solid var(--line); }
  .brow:first-of-type { border-top: 0; }
  .bl { font-weight: 500; display: flex; flex-direction: column; gap: 3px; align-items: flex-start; }
  .bars { display: grid; grid-template-columns: 1fr 44px; row-gap: 2px; align-items: center; }
  .bar { height: 12px; border-radius: 0 4px 4px 0; min-width: 2px; cursor: default; }
  .bar.you { background: var(--you); } .bar.mkt { background: var(--mkt); }
  .bar:hover { filter: brightness(1.1); }
  .bv { font-size: 12px; color: var(--ink-2); font-variant-numeric: tabular-nums; padding-left: 8px; }
  .flag { font-size: 11px; font-weight: 600; padding: 1px 8px; border-radius: 999px; }
  .flag::before { margin-right: 4px; }
  .flag.under { color: var(--bad); background: color-mix(in srgb, var(--bad) 12%, transparent); } .flag.under::before { content: "▲"; font-size: 8px; }
  .flag.over { color: var(--warn); background: color-mix(in srgb, var(--warn) 14%, transparent); } .flag.over::before { content: "●"; font-size: 8px; }
  .flag.ok { color: var(--good); background: color-mix(in srgb, var(--good) 12%, transparent); } .flag.ok::before { content: "✓"; }
  details.tbl { margin-top: 12px; font-size: 13px; } details.tbl summary { cursor: pointer; color: var(--ink-2); }
  details.tbl table, table.kw, table.qa { width: 100%; border-collapse: collapse; }
  details.tbl td, details.tbl th { padding: 6px 8px; border-bottom: 1px solid var(--line); text-align: left; }
  #tip { position: fixed; pointer-events: none; background: var(--ink); color: var(--bg); font-size: 12.5px; padding: 6px 10px; border-radius: 8px; opacity: 0; transition: opacity .1s; z-index: 20; }

  /* Gaps */
  table.gaps { width: 100%; border-collapse: collapse; }
  table.gaps td, table.gaps th { padding: 12px 10px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; font-size: 14px; }
  table.gaps th { font-size: 11px; letter-spacing: .08em; text-transform: uppercase; color: var(--ink-3); font-weight: 600; }
  table.gaps tr:last-child td { border-bottom: 0; }
  .gk { font-weight: 600; font-size: 15px !important; white-space: nowrap; }
  .gp { white-space: nowrap; font-variant-numeric: tabular-nums; }
  .gw { color: var(--ink-2); } .gw small { display: block; color: var(--ink-3); font-size: 12px; margin-top: 2px; }

  /* Keywords */
  .search { width: 100%; max-width: 360px; font: inherit; padding: 9px 12px; border-radius: 10px; border: 1px solid var(--line); background: var(--surface); color: var(--ink); margin-bottom: 12px; }
  table.kw td, table.kw th, table.qa td, table.qa th { padding: 10px 10px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; font-size: 14px; }
  table.kw th, table.qa th { font-size: 11px; letter-spacing: .08em; text-transform: uppercase; color: var(--ink-3); font-weight: 600; }
  .kp { display: flex; gap: 10px; align-items: flex-start; min-width: 240px; }
  .kp img { width: 44px; height: 44px; object-fit: contain; background: #fff; border-radius: 8px; border: 1px solid var(--line); flex: none; }
  .kp a { color: var(--ink); font-weight: 600; text-decoration: none; } .kp a:hover { text-decoration: underline; }
  .kp small { display: block; color: var(--ink-3); font-size: 12px; }
  .tags { color: var(--ink-3); font-size: 12.5px !important; max-width: 220px; }
  .chip { display: inline-block; font-size: 12.5px; padding: 2px 9px; border-radius: 999px; margin: 0 4px 4px 0; }
  .chip.add { background: color-mix(in srgb, var(--you) 12%, transparent); color: var(--you); font-weight: 600; }
  .chip.ok { background: color-mix(in srgb, var(--good) 10%, transparent); color: var(--good); }
  .none { color: var(--ink-3); font-size: 13px; }
  .scroll { overflow-x: auto; }

  /* QA */
  .qhits { display: flex; gap: 8px; flex-wrap: wrap; }
  .qhit { display: inline-flex; gap: 6px; align-items: center; font-size: 12.5px; color: var(--ink-2); max-width: 240px; }
  .qhit img { width: 30px; height: 30px; object-fit: contain; background: #fff; border-radius: 6px; border: 1px solid var(--line); }
  table.qa small { color: var(--ink-3); font-weight: 400; margin-left: 4px; }

  footer { padding: 40px 0 60px; color: var(--ink-3); font-size: 13px; }
  .method { color: var(--ink-2); font-size: 14px; }
  .method li { margin-bottom: 6px; }
  .empty { color: var(--ink-2); }
  @media (max-width: 760px) {
    .stats { grid-template-columns: 1fr 1fr; }
    .brow { grid-template-columns: 1fr; gap: 6px; }
    .wrap { padding: 0 16px; }
  }
</style>
</head>
<body>
<div class="topbar"><div class="wrap">
  <span class="brand"><span class="logo"><svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round"><circle cx="11" cy="11" r="6"/><path d="m20 20-4.2-4.2M8.5 11h5M11 8.5v5"/></svg></span>Catalog X-ray</span>
  <a id="back" href="/" hidden>← Scan another store</a>
</div></div>
<header class="wrap">
  <div class="kicker">Report · {{DATE}}</div>
  <h1>{{DOMAIN}}</h1>
  <p class="lede">{{N_PRODUCTS}} products ({{N_MODELS}} models once colour and size variants are grouped), read from {{SOURCE}}, and compared with {{MARKET}} similar products BehaviorGPT knows shoppers buy.</p>
  <p class="headline">{{HEADLINE}}</p>
  <div class="stats">
    <div class="stat"><b>{{N_GAPS}}</b><span>kinds of products shoppers around your range want that you don’t carry</span></div>
    <div class="stat"><b>{{N_KW}}</b><span>keywords to add, across {{N_MISSING_MODELS}} products</span></div>
    <div class="stat"><b>{{N_WEAK}}</b><span>of {{N_QUERIES}} key searches return weak results</span></div>
    <div class="stat"><b>{{N_MODELS}}</b><span>product models analysed</span></div>
  </div>
</header>

<nav><div class="wrap"><a href="#assortment">Assortment balance</a><a href="#gaps">What to add</a><a href="#keywords">Keywords</a><a href="#search">Search QA</a><a href="#method">How it works</a></div></nav>

<main class="wrap">
  <section class="block" id="assortment">
    <h2>Assortment balance</h2>
    <p class="sub">Each of your products gets the product area BehaviorGPT assigns to its closest market equivalent. Blue is your share of models in that area; orange is the area’s share of what shoppers buy in your part of the market.</p>
    <div class="card">
      <div class="legend"><span><i style="background:var(--you)"></i>Your catalog (share of models)</span><span><i style="background:var(--mkt)"></i>Market demand (share of sales)</span></div>
      {{BALANCE}}
    </div>
  </section>

  <section class="block" id="gaps">
    <h2>What to add</h2>
    <p class="sub">Kinds of products that shoppers look at alongside, or right after, products like yours, which your catalog doesn’t carry. Ranked by how popular they are. Types come from the names of popular market products of that kind; prices are the typical US market range.</p>
    {{GAPS}}
    <a class="dl" href="{{CSV_GAPS}}" download>Download all gaps as CSV ↓</a>
  </section>

  <section class="block" id="keywords">
    <h2>Keywords to add</h2>
    <p class="sub">Words shoppers use for products like yours that don’t find your product in your own catalog today. Add them to the product’s tags or title. Hover a suggestion to see why. Words that already find it are shown in green.</p>
    <input class="search" id="kwq" type="search" placeholder="Filter products or keywords…">
    <div class="card scroll"><table class="kw" id="kwt">
      <tr><th>Product</th><th>Current tags</th><th>Add these</th><th>Already found by</th></tr>
      {{KEYWORDS}}
    </table></div>
    <a class="dl" href="{{CSV_KW}}" download>Download keywords as CSV ↓</a>
  </section>

  <section class="block" id="search">
    <h2>Search QA</h2>
    <p class="sub">The kinds of product you sell and the most common missing keywords, searched in your own catalog. For a kind of product, “weak” means fewer than 3 of your top 5 results are that kind. For a descriptive word, it means fewer than 2 of your top 5 results mention it: shoppers who search it won’t see what they asked for.</p>
    <div class="card scroll"><table class="qa">
      <tr><th>Search</th><th>Result</th><th>Your top results</th></tr>
      {{QA}}
    </table></div>
    <a class="dl" href="{{CSV_QA}}" download>Download search QA as CSV ↓</a>
  </section>

  <section class="block method" id="method">
    <h2>How it works</h2>
    <ul>
      <li><b>Your catalog</b> was uploaded to BehaviorGPT, a model trained on long sequences of what shoppers view, add to cart and buy.</li>
      <li><b>The market</b> is BehaviorGPT’s reference retail catalog, which carries sales history. For each of your products we take its 10 closest market products, plus what shoppers go on to want after viewing them.</li>
      <li><b>Each product is described by what it is</b> (“women’s running shoes”), because branded names mislead search. Where the category is vague, your own catalog is searched to find the most specific kind each product ranks for.</li>
      <li><b>Gaps</b> are kinds of products among your market, or among what its shoppers look at next, that you carry nothing of.</li>
      <li><b>Keywords</b> come from a shopper vocabulary: words and phrases that at least three brands use in their titles across your market. A product gets a suggestion when most of its market neighbours use the word, or when its own description uses it but its name and tags don’t, and searching your catalog for it (with the product kind, e.g. “breathable running shoes”) doesn’t return the product in the top 20.</li>
      <li>The market is a general US retail catalog, so niche or local assortments will match it less closely.</li>
    </ul>
  </section>
</main>
<footer class="wrap">Generated by <a href="https://github.com/Jenspalmborg/catalog-xray">catalog-xray</a> with <a href="https://github.com/Unbox-AI/behaviorgpt">BehaviorGPT</a>.</footer>
<div id="tip"></div>
<script>
  // Opened from the web app: offer the way back to the scan page.
  if (location.pathname.startsWith("/reports/")) document.getElementById("back").hidden = false;
  const tip = document.getElementById("tip");
  document.querySelectorAll("[data-tip]").forEach(el => {
    el.addEventListener("mousemove", ev => { tip.textContent = el.dataset.tip; tip.style.opacity = 1;
      tip.style.left = Math.min(innerWidth - 240, ev.clientX + 12) + "px"; tip.style.top = (ev.clientY + 14) + "px"; });
    el.addEventListener("mouseleave", () => tip.style.opacity = 0);
  });
  const q = document.getElementById("kwq");
  q && q.addEventListener("input", () => {
    const v = q.value.trim().toLowerCase();
    document.querySelectorAll("#kwt tr[data-q]").forEach(tr => tr.hidden = v && !tr.dataset.q.includes(v));
  });
</script>
</body>
</html>
"""
