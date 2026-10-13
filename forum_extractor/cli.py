"""Command-line interface.

  python -m forum_extractor serve                 # local web UI (default http://127.0.0.1:8765)
  python -m forum_extractor analyze URL           # detection report + extraction preview
  python -m forum_extractor test-thread URL       # live single-thread test
  python -m forum_extractor run URL [options]     # run a collection job in the terminal
  python -m forum_extractor resume JOB_ID         # continue an interrupted job
  python -m forum_extractor retry JOB_ID          # retry failed threads of a job
  python -m forum_extractor jobs                  # list jobs
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .browser import BrowserManager
from .config import JobConfig
from .engine import JobManager
from .logging_setup import setup_logging
from .paths import data_dir
from .profiles import ProfileStore
from .service import analyze_url, effective_profile, test_thread
from .storage import Database


def _job_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--out", dest="output_dir", default="output", help="output folder")
    p.add_argument("--max-pages", type=int, default=None, help="maximum number of listing pages")
    p.add_argument("--pages-per-file", type=int, default=1100)
    p.add_argument("--delay", dest="request_delay", type=float, default=1.0, help="seconds between requests")
    p.add_argument("--concurrency", dest="max_concurrency", type=int, default=2)
    p.add_argument("--threshold", dest="confidence_threshold", type=float, default=0.6)
    p.add_argument("--manual", action="store_true", help="use only the saved site profile selectors")
    p.add_argument("--no-skip-duplicates", action="store_true")
    p.add_argument("--keep-quotes", action="store_true", help="keep quoted text inside the original post")
    p.add_argument("--ignore-robots", action="store_true", help="do not consult robots.txt")
    p.add_argument("--prefix", dest="file_prefix", default="Forum_Pages")
    p.add_argument("--timeout", dest="page_timeout", type=float, default=30.0)
    p.add_argument("--headful", action="store_true", help="show the browser window")
    p.add_argument("--ai", dest="ai_classifier", choices=["none", "ollama"], default="none")


def _cfg(args, url: str) -> JobConfig:
    return JobConfig.from_dict({
        "start_url": url, "output_dir": args.output_dir, "max_pages": args.max_pages,
        "pages_per_file": args.pages_per_file, "request_delay": args.request_delay,
        "max_concurrency": args.max_concurrency, "confidence_threshold": args.confidence_threshold,
        "mode": "manual" if args.manual else "auto", "skip_duplicates": not args.no_skip_duplicates,
        "exclude_quotes": not args.keep_quotes, "respect_robots": not args.ignore_robots,
        "file_prefix": args.file_prefix, "page_timeout": args.page_timeout, "headless": not args.headful,
        "ai_classifier": args.ai_classifier, "on_unknown_pagination": "stop",
    })


async def _watch(manager: JobManager, job_id: str) -> int:
    last = None
    while manager.is_running(job_id):
        st = manager.status(job_id)
        line = (f"[{st['status']}] page {st['current_page_number'] or '-'} | done {st['threads_completed']} "
                f"| failed {st['threads_failed']} | dup {st['threads_duplicate']} | skipped {st['threads_skipped']} "
                f"| discovered {st['threads_discovered']}")
        if line != last:
            print(line, flush=True)
            last = line
        await asyncio.sleep(1)
    await manager.tasks[job_id]
    st = manager.status(job_id)
    print(json.dumps({k: st[k] for k in ("status", "message", "threads_completed", "threads_failed",
                                         "threads_duplicate", "threads_skipped", "output_files")}, indent=2))
    return 0 if st["status"] == "completed" else 1


async def _run_job(db, store, job_id: str, mode: str) -> int:
    manager = JobManager(db, store)
    manager.start(job_id, mode)
    try:
        return await _watch(manager, job_id)
    except (KeyboardInterrupt, asyncio.CancelledError):
        manager.runner(job_id).stop()
        await manager.tasks[job_id]
        print("stopped; resume with: python -m forum_extractor resume", job_id)
        return 130


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="forum_extractor", description="Universal forum original-post extractor")
    ap.add_argument("--data-dir", default=None, help="state folder (database, profiles, logs); default ./data")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="start the local web interface")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)

    a = sub.add_parser("analyze", help="analyse a URL and preview the extraction")
    a.add_argument("url")
    a.add_argument("--thread", default=None, help="thread URL to use for the preview")
    _job_args(a)

    t = sub.add_parser("test-thread", help="extract the original post of a single thread")
    t.add_argument("url")
    _job_args(t)

    r = sub.add_parser("run", help="run a collection job")
    r.add_argument("url")
    _job_args(r)

    for name in ("resume", "retry"):
        x = sub.add_parser(name)
        x.add_argument("job_id")
    sub.add_parser("jobs", help="list jobs")

    args = ap.parse_args(argv)
    ddir = data_dir(args.data_dir)
    setup_logging(ddir / "logs")

    if args.cmd == "serve":
        import uvicorn
        from .web.app import create_app
        print(f"Forum extractor UI: http://{args.host}:{args.port}")
        uvicorn.run(create_app(str(ddir)), host=args.host, port=args.port, log_level="warning")
        return 0

    db = Database(ddir / "state.sqlite3")
    store = ProfileStore(ddir / "profiles")
    db.mark_interrupted_jobs()

    if args.cmd == "jobs":
        for j in db.list_jobs():
            c = db.thread_counts(j["id"])
            print(f"{j['id']}  {j['status']:<12} done={c.get('done', 0):<6} failed={c.get('failed', 0):<4} {j['start_url']}")
        return 0
    if args.cmd in ("resume", "retry"):
        if not db.get_job(args.job_id):
            print("unknown job", args.job_id, file=sys.stderr)
            return 2
        return asyncio.run(_run_job(db, store, args.job_id, "crawl" if args.cmd == "resume" else "retry_failed"))
    if args.cmd == "run":
        cfg = _cfg(args, args.url)
        job_id = db.create_job(cfg.start_url, cfg.to_dict())
        print("job", job_id)
        return asyncio.run(_run_job(db, store, job_id, "crawl"))

    cfg = _cfg(args, args.url)

    async def probe() -> dict:
        b = BrowserManager(headless=cfg.headless, timeout=cfg.page_timeout, max_retries=cfg.max_retries)
        try:
            if args.cmd == "analyze":
                return await analyze_url(b, args.url, store, cfg, preview_thread=args.thread)
            return await test_thread(b, args.url, effective_profile(store, args.url, None), cfg)
        finally:
            await b.close()

    out = asyncio.run(probe())
    if args.cmd == "analyze":
        an = out["analysis"]
        print(f"Page type : {an['page_type']} ({an['page_type_confidence']})")
        print(f"Platform  : {an['platform_label']} ({an['platform_confidence']})")
        print(f"Page no.  : {an['page_number']}")
        print(f"Threads   : {an['threads']['count']} via {an['threads']['method']} "
              f"(confidence {an['threads']['confidence']}) selector={an['threads']['selector']!r}")
        for it in an["threads"]["items"][:10]:
            print("   -", it["title"], "->", it["url"])
        pg = an["pagination"]
        print(f"Next page : {pg['next_url']} [{pg['method']}] confidence {pg['confidence']}")
        if pg["needs_confirmation"]:
            print("  Pagination unclear; candidate links:")
            for c in pg["candidates"][:10]:
                print("   ?", c["url"], c["text"], c["reason"])
        prev = out.get("preview") or {}
    else:
        prev = out
    print("-" * 80)
    print(f"Preview   : ok={prev.get('ok')} confidence={prev.get('confidence')} method={prev.get('method')}")
    if prev.get("error"):
        print("Error     :", prev["error"])
    print(f"Selectors : container={prev.get('container_selector')!r} body={prev.get('body_selector')!r}")
    print("-" * 80)
    print(prev.get("text", ""))
    return 0 if prev.get("ok") else 1
