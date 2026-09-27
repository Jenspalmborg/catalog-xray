"""xray <store-url>: scrape, upload, analyse and report from the command line.
xray-web: the same, from a page in your browser.
"""

import argparse
import sys
import webbrowser
from pathlib import Path

from xray.pipeline import OUT, ScanError, scan, scan_file


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="xray", description="Catalog X-ray: assortment gaps, keywords and search QA for any store."
    )
    ap.add_argument("url", nargs="?", help="store URL, e.g. https://www.some-store.com")
    ap.add_argument("--file", type=Path, help="analyse a product export (CSV) instead of scanning a website")
    ap.add_argument("--name", help="store name for the report when using --file")
    ap.add_argument(
        "--max", type=int, default=5000, help="max products to scrape (default 5000, limit 20000)"
    )
    ap.add_argument(
        "--queries", type=Path, help="text file with your own search queries to test, one per line"
    )
    ap.add_argument("--out", type=Path, default=OUT, help="output folder (default ./out in the project)")
    ap.add_argument(
        "--delay", type=float, default=1.0, help="seconds between requests to the store (default 1)"
    )
    ap.add_argument("--fresh", action="store_true", help="scrape again even if a previous scrape exists")
    ap.add_argument("--no-open", action="store_true", help="don't open the report in a browser")
    args = ap.parse_args()

    queries = (
        [q.strip() for q in args.queries.read_text().splitlines() if q.strip()] if args.queries else None
    )
    if not args.url and not args.file:
        ap.error("give a store URL or --file export.csv")
    try:
        if args.file:
            path = scan_file(args.file.read_bytes(), args.name or args.file.stem, queries, args.out)
        else:
            path = scan(args.url, args.max, args.fresh, queries, args.delay, args.out)
    except ScanError as exc:
        sys.exit(str(exc))
    print(f"Report: {path.resolve()}")
    if not args.no_open:
        webbrowser.open(path.resolve().as_uri())


def web() -> None:
    import uvicorn

    from xray.web import app

    port = 8765
    print(f"Catalog X-ray is running at http://127.0.0.1:{port}  (Ctrl+C to stop)")
    webbrowser.open(f"http://127.0.0.1:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
