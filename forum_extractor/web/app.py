"""Local web interface (FastAPI). Binds to 127.0.0.1 by default; intended for a single local user."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import platform
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response

from ..browser import BlockedError, BrowserManager, FetchError
from ..config import JobConfig, SiteProfile
from ..engine import JobManager
from ..logging_setup import setup_logging
from ..paths import data_dir
from ..profiles import ProfileStore
from ..service import analyze_url, effective_profile, test_thread
from ..storage import Database
from ..urls import domain_key

STATIC = Path(__file__).parent / "static"
RESUMABLE = ("stopped", "interrupted", "error", "blocked", "paused", "awaiting_confirmation", "created")


class Probe:
    """A shared browser for analysis / preview requests (separate from job browsers)."""

    def __init__(self) -> None:
        self.browser: BrowserManager | None = None
        self.lock = asyncio.Lock()
        self.settings: tuple | None = None

    async def get(self, cfg: JobConfig) -> BrowserManager:
        key = (cfg.headless, cfg.page_timeout, cfg.user_agent)
        if self.browser is None or self.settings != key:
            if self.browser is not None:
                await self.browser.close()
            self.browser = BrowserManager(headless=cfg.headless, timeout=cfg.page_timeout, max_retries=1,
                                          user_agent=cfg.user_agent, wait_for_network_idle=cfg.wait_for_network_idle)
            self.settings = key
        return self.browser

    async def close(self) -> None:
        if self.browser is not None:
            await self.browser.close()
            self.browser = None


def create_app(data: str | None = None) -> FastAPI:
    ddir = data_dir(data)
    setup_logging(ddir / "logs", console=False)
    db = Database(ddir / "state.sqlite3")
    store = ProfileStore(ddir / "profiles")
    manager = JobManager(db, store)
    probe = Probe()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db.mark_interrupted_jobs()
        yield
        await manager.shutdown()
        await probe.close()

    app = FastAPI(title="Forum Post Extractor", lifespan=lifespan)
    app.state.db, app.state.store, app.state.manager = db, store, manager

    def cfg_from(payload: dict) -> JobConfig:
        try:
            return JobConfig.from_dict(payload)
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, str(exc))

    @app.exception_handler(FetchError)
    async def _fetch_err(request, exc):
        return JSONResponse({"detail": f"Could not load the page: {exc}"}, status_code=502)

    @app.exception_handler(Exception)
    async def _any_error(request, exc):
        import logging
        import traceback
        logging.getLogger("forum_extractor.web").error("request failed: %r", exc, exc_info=exc)
        traceback.print_exception(exc)
        msg = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
        hint = ""
        if isinstance(exc, NotImplementedError):
            hint = (" (Windows event-loop problem: start the app with 'python -m forum_extractor serve', "
                    "not with uvicorn directly)")
        elif "Executable doesn't exist" in str(exc) or "Could not launch Chromium" in str(exc):
            hint = " (Chromium is missing: run 'python -m playwright install chromium')"
        return JSONResponse({"detail": f"{type(exc).__name__}: {msg[:500]}{hint}"}, status_code=500)

    @app.exception_handler(BlockedError)
    async def _blocked(request, exc):
        return JSONResponse({"detail": f"Access blocked: {exc}"}, status_code=403)

    # ------------------------------------------------------------------ pages
    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/defaults")
    async def defaults():
        cfg = JobConfig(start_url="https://example.com/")
        d = cfg.to_dict()
        d["start_url"] = ""
        d["output_dir"] = str(Path("output").resolve())
        return {"config": d, "data_dir": str(ddir.resolve())}

    # ------------------------------------------------------------------ analysis / preview
    @app.post("/api/analyze")
    async def analyze(payload: dict = Body(...)):
        cfg = cfg_from(payload.get("config", {}))
        async with probe.lock:
            browser = await probe.get(cfg)
            return await analyze_url(browser, cfg.start_url, store, cfg, payload.get("profile"),
                                     payload.get("thread_url") or None)

    @app.post("/api/test-thread")
    async def test(payload: dict = Body(...)):
        cfg = cfg_from(payload.get("config", {}))
        url = payload.get("url") or ""
        if not url.startswith(("http://", "https://")):
            raise HTTPException(400, "thread URL must start with http:// or https://")
        prof = effective_profile(store, url, payload.get("profile"))
        async with probe.lock:
            browser = await probe.get(cfg)
            return await test_thread(browser, url, prof, cfg, payload.get("title", ""))

    # ------------------------------------------------------------------ profiles
    @app.get("/api/profiles")
    async def profiles():
        return [p.to_dict() for p in store.list()]

    @app.get("/api/profiles/{domain}")
    async def get_profile(domain: str):
        p = store.get(domain)
        if not p:
            raise HTTPException(404, "no profile saved for this domain")
        return p.to_dict()

    @app.put("/api/profiles/{domain}")
    async def put_profile(domain: str, payload: dict = Body(...)):
        payload = dict(payload)
        payload["domain"] = domain
        payload["source"] = "user"
        return store.save(SiteProfile.from_dict(payload)).to_dict()

    @app.delete("/api/profiles/{domain}")
    async def delete_profile(domain: str):
        return {"deleted": store.delete(domain)}

    # ------------------------------------------------------------------ jobs
    @app.get("/api/jobs")
    async def jobs():
        out = []
        for j in db.list_jobs(100):
            c = db.thread_counts(j["id"])
            out.append({"id": j["id"], "start_url": j["start_url"], "status": j["status"],
                        "running": manager.is_running(j["id"]), "created_at": j["created_at"],
                        "done": c.get("done", 0), "failed": c.get("failed", 0), "platform": j["platform"],
                        "output_dir": j["config"].get("output_dir")})
        return out

    @app.post("/api/jobs")
    async def create_job(payload: dict = Body(...)):
        cfg = cfg_from(payload.get("config", {}))
        if payload.get("profile"):
            prof = effective_profile(store, cfg.start_url, payload["profile"])
            if prof:
                store.save(prof)
        if payload.get("resume_previous", True):
            for j in db.list_jobs(200):
                if j["start_url"] == cfg.start_url and j["status"] in RESUMABLE and not manager.is_running(j["id"]) \
                        and Path(j["config"].get("output_dir", "")).expanduser().resolve() == \
                        Path(cfg.output_dir).expanduser().resolve():
                    db.update_job(j["id"], config=cfg.to_dict())
                    manager.start(j["id"], "crawl")
                    return {"job_id": j["id"], "resumed": True}
        job_id = manager.create(cfg)
        manager.start(job_id, "crawl")
        return {"job_id": job_id, "resumed": False}

    def _job(job_id: str) -> dict:
        try:
            return manager.status(job_id)
        except KeyError:
            raise HTTPException(404, "unknown job")

    @app.get("/api/jobs/{job_id}")
    async def job_status(job_id: str):
        return _job(job_id)

    @app.post("/api/jobs/{job_id}/{action}")
    async def job_action(job_id: str, action: str, payload: dict = Body(default={})):
        _job(job_id)
        runner = manager.runner(job_id)
        running = manager.is_running(job_id)
        if action == "pause" and running:
            runner.pause()
        elif action == "resume":
            if running:
                runner.resume()
            else:
                manager.start(job_id, "crawl")
        elif action == "stop" and running:
            runner.stop()
        elif action == "retry":
            if running:
                raise HTTPException(409, "stop or wait for the job to finish before retrying failed threads")
            manager.start(job_id, "retry_failed")
        elif action == "confirm" and running:
            runner.confirm_next(payload.get("url") or None)
        elif action in ("pause", "stop", "confirm"):
            raise HTTPException(409, "job is not running")
        else:
            raise HTTPException(400, f"unknown action {action}")
        return _job(job_id)

    @app.get("/api/jobs/{job_id}/failed")
    async def failed(job_id: str):
        return db.failed_threads(job_id)

    @app.get("/api/jobs/{job_id}/log")
    async def export_log(job_id: str, format: str = "jsonl"):
        _job(job_id)
        events = db.events(job_id)
        threads = db._all("SELECT page_number, title, thread_url, status, confidence, method, error, retry_count, "
                          "output_file FROM threads WHERE job_id=? ORDER BY page_seq, position", (job_id,))
        if format == "csv":
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["listing_page", "title", "thread_url", "status", "confidence", "method", "error",
                        "retry_count", "output_file"])
            for t in threads:
                w.writerow([t["page_number"], t["title"], t["thread_url"], t["status"], t["confidence"], t["method"],
                            t["error"], t["retry_count"], t["output_file"]])
            return Response(buf.getvalue(), media_type="text/csv",
                            headers={"Content-Disposition": f'attachment; filename="job_{job_id}_threads.csv"'})
        lines = [json.dumps({k: e[k] for k in ("ts", "level", "event", "message")} | {"data": json.loads(e["data"])},
                            ensure_ascii=False) for e in events]
        return PlainTextResponse("\n".join(lines) + "\n", media_type="application/x-ndjson",
                                 headers={"Content-Disposition": f'attachment; filename="job_{job_id}_log.jsonl"'})

    @app.get("/api/jobs/{job_id}/files/{index}")
    async def download_file(job_id: str, index: int):
        files = _job(job_id)["output_files"]
        if not 0 <= index < len(files) or not os.path.exists(files[index]):
            raise HTTPException(404, "file not found (it may still be empty)")
        return FileResponse(files[index], media_type="text/plain; charset=utf-8",
                            filename=os.path.basename(files[index]))

    @app.get("/api/jobs/{job_id}/output-preview")
    async def output_preview(job_id: str, chars: int = 6000):
        st = _job(job_id)
        path = st.get("current_output_file") or (st["output_files"][-1] if st["output_files"] else "")
        if not path or not os.path.exists(path):
            return {"file": path, "text": ""}
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            fh.seek(max(0, size - chars))
            text = fh.read().decode("utf-8", errors="ignore")
        return {"file": path, "text": text, "size": size}

    # ------------------------------------------------------------------ local folder helpers
    @app.post("/api/open-folder")
    async def open_folder(payload: dict = Body(...)):
        path = Path(payload.get("path") or "output").expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        try:
            system = platform.system()
            if system == "Windows":
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif system == "Darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {"opened": True, "path": str(path)}
        except Exception as exc:
            return {"opened": False, "path": str(path), "error": str(exc)}

    @app.post("/api/choose-folder")
    async def choose_folder(payload: dict = Body(default={})):
        def pick() -> str:
            try:
                import tkinter
                from tkinter import filedialog
                root = tkinter.Tk()
                root.withdraw()
                root.attributes("-topmost", True)
                chosen = filedialog.askdirectory(initialdir=payload.get("path") or str(Path.cwd()))
                root.destroy()
                return chosen or ""
            except Exception:
                return ""
        chosen = await asyncio.to_thread(pick)
        return {"path": chosen, "available": bool(chosen)}

    @app.get("/api/domain")
    async def domain(url: str):
        return {"domain": domain_key(url)}

    return app


def _app_from_env() -> Any:  # for `uvicorn forum_extractor.web.app:app`
    return create_app()
