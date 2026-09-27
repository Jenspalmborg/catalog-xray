"""A local web page: paste a store URL, watch the scan, read the report.

Runs on your machine with your API key. One scan at a time: the key holds a
single catalog, so two stores can't be uploaded at once.

Run:  uv run xray-web
"""

import json
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from xray.pipeline import OUT, STEPS, ScanError, domain_of, scan, scan_file

PAGE = Path(__file__).parent / "static" / "index.html"
OUT.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Catalog X-ray")
app.mount("/reports", StaticFiles(directory=OUT, html=True), name="reports")

jobs: dict[str, dict] = {}
busy = threading.Lock()


class ScanRequest(BaseModel):
    url: str = Field(min_length=4, max_length=300)
    max_products: int = Field(default=1000, ge=20, le=20000)
    fresh: bool = False


def run_job(job: dict) -> None:
    def step(i: int, msg: str) -> None:
        job["step"] = i
        job["log"].append({"t": time.time(), "msg": msg, "step": True})

    def log(msg: str) -> None:
        job["log"].append({"t": time.time(), "msg": msg.strip(), "step": False})

    try:
        if job.get("data") is not None:
            path = scan_file(job.pop("data"), job["domain"], step=step, log=log)
        else:
            path = scan(job["url"], job["max_products"], job["fresh"], step=step, log=log)
        job["report"] = f"/reports/{path.parent.name}/"
        job["status"] = "done"
    except ScanError as exc:
        job["status"], job["error"] = "error", str(exc)
    except Exception as exc:  # anything unexpected still ends the job cleanly
        job["status"], job["error"] = "error", f"Something went wrong: {type(exc).__name__}: {exc}"
    finally:
        job["finished"] = time.time()
        busy.release()


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse(PAGE.read_text())


@app.post("/api/scan")
def start(req: ScanRequest) -> dict:
    if not busy.acquire(blocking=False):
        running = next((j for j in jobs.values() if j["status"] == "running"), None)
        raise HTTPException(
            409, f"A scan of {running['domain'] if running else 'another store'} is still running."
        )
    job = {
        "id": uuid.uuid4().hex[:10],
        "url": req.url.strip(),
        "domain": domain_of(req.url.strip()),
        "max_products": req.max_products,
        "fresh": req.fresh,
        "status": "running",
        "step": 0,
        "log": [],
        "started": time.time(),
        "report": None,
        "error": None,
    }
    jobs[job["id"]] = job
    threading.Thread(target=run_job, args=(job,), daemon=True).start()
    return {"id": job["id"], "steps": STEPS}


@app.post("/api/upload")
async def upload(file: UploadFile = File(...), name: str = Form("")) -> dict:
    """Analyse a product export: nothing is fetched from the store."""
    data = await file.read()
    if len(data) > 50_000_000:
        raise HTTPException(413, "That file is over 50 MB.")
    if not busy.acquire(blocking=False):
        raise HTTPException(409, "Another scan is still running.")
    label = (name or Path(file.filename or "upload").stem).strip()
    job = {
        "id": uuid.uuid4().hex[:10],
        "url": "",
        "domain": label,
        "max_products": 0,
        "fresh": False,
        "data": data,
        "status": "running",
        "step": 0,
        "log": [],
        "started": time.time(),
        "report": None,
        "error": None,
    }
    jobs[job["id"]] = job
    threading.Thread(target=run_job, args=(job,), daemon=True).start()
    return {"id": job["id"], "steps": STEPS}


@app.get("/api/scan/{job_id}")
def status(job_id: str) -> dict:
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "No such scan.")
    return {k: job[k] for k in ("id", "domain", "status", "step", "log", "report", "error", "started")} | {
        "steps": STEPS
    }


@app.get("/api/reports")
def reports() -> list[dict]:
    """Earlier scans, newest first."""
    out = []
    for page in OUT.glob("*/index.html"):
        meta = page.parent / "result.json"
        try:
            r = json.loads(meta.read_text())
            summary = {
                "products": r["products"],
                "gaps": len(r["gaps"]),
                "keywords": sum(len(m["missing"]) for m in r["models"]),
            }
        except (OSError, ValueError, KeyError):
            summary = {}
        out.append(
            {
                "domain": page.parent.name,
                "url": f"/reports/{page.parent.name}/",
                "updated": page.stat().st_mtime,
                **summary,
            }
        )
    return sorted(out, key=lambda r: -r["updated"])


@app.get("/favicon.ico")
def favicon() -> FileResponse:
    raise HTTPException(404)
