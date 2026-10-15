"""Job orchestration: listing-page loop, bounded-concurrency thread extraction, ordered incremental output."""
from __future__ import annotations

import asyncio
import hashlib
import time
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from .browser import BlockedError, BrowserManager, FetchError, PageSnapshot
from .classifier import make_classifier
from .config import JobConfig, SiteProfile
from .detection import HARD_BLOCKS, analyze_html, detect_blocking, detect_platform, suggest_profile
from .extraction import extract_original_post
from .platforms import PlatformSpec, get_platform
from .profiles import ProfileStore
from .storage import DONE_STATES, Database
from .textutil import soup_from_html
from .urls import canonical_thread_url, domain_key, in_scope, normalize_url, thread_key
from .writer import OutputWriter, WriterError

log = logging.getLogger("forum_extractor.engine")



@dataclass
class Outcome:
    status: str  # done | failed | skipped | duplicate
    text: str = ""
    confidence: float | None = None
    method: str = ""
    error: str = ""
    title: str = ""


class JobRunner:
    def __init__(self, db: Database, profiles: ProfileStore, job_id: str, config: JobConfig,
                 browser: BrowserManager | None = None):
        self.db = db
        self.profiles = profiles
        self.job_id = job_id
        self.cfg = config
        self.browser = browser
        self._own_browser = browser is None
        self.writer: OutputWriter | None = None
        self.state = "created"
        self.stop_requested = False
        self._pause = asyncio.Event()
        self._pause.set()
        self._write_lock = asyncio.Lock()
        self._confirm: asyncio.Future | None = None
        self.pending_confirmation: dict | None = None
        self.domain = domain_key(config.start_url)
        self.profile: SiteProfile | None = None
        self.spec: PlatformSpec | None = None
        self.classifier = make_classifier(config.ai_classifier, config.ai_model, config.ai_endpoint)
        self.current_page_number: int | None = None
        self.current_page_url = ""
        self.last_confidence: float | None = None
        # live progress for the UI
        self.activity = "Starting…"
        self.page_total = 0
        self.page_done = 0
        self.in_flight: dict[int, str] = {}
        self.started_monotonic = 0.0
        self.done_this_run = 0

    # ------------------------------------------------------------------ controls
    def pause(self) -> None:
        if self.state == "running":
            self._pause.clear()
            self._set_state("paused", "paused by user")
            self.activity = "Paused – press Resume to continue"

    def resume(self) -> None:
        if self.state == "paused":
            self._pause.set()
            self._set_state("running", "")
            self.activity = "Resuming…"

    def stop(self) -> None:
        self.stop_requested = True
        self._pause.set()
        if self._confirm is not None and not self._confirm.done():
            self._confirm.set_result(None)
        if self.state in ("running", "paused", "awaiting_confirmation"):
            self._set_state("stopping", "stopping after in-flight requests")
            self.activity = "Stopping – finishing the requests already in progress…"

    def confirm_next(self, url: str | None) -> None:
        if self._confirm is not None and not self._confirm.done():
            self._confirm.set_result(url)

    # ------------------------------------------------------------------ helpers
    def _set_state(self, state: str, message: str | None = None) -> None:
        self.state = state
        fields = {"status": state}
        if message is not None:
            fields["message"] = message
        self.db.update_job(self.job_id, **fields)

    def _log(self, level: str, event: str, message: str, **data) -> None:
        self.db.log_event(self.job_id, level, event, message, **data)
        getattr(log, "warning" if level == "warn" else level, log.info)(
            message, extra={"job_id": self.job_id, "event": event})

    async def _wait_if_paused(self) -> None:
        await self._pause.wait()

    def _load_profile(self) -> None:
        self.profile = self.profiles.get(self.domain)
        if self.cfg.mode == "manual" and (self.profile is None or not self.profile.has_post_selectors()):
            raise ValueError(f"Manual selector mode requires a saved site profile for {self.domain}")

    # ------------------------------------------------------------------ main entry
    async def run(self, mode: str = "crawl") -> None:
        self.stop_requested = False
        self.started_monotonic = time.monotonic()
        self.done_this_run = 0
        self.activity = "Starting the browser…" if mode == "crawl" else "Retrying failed threads…"
        self._set_state("running", "")
        self._log("info", "job_start", f"job {mode} started", start_url=self.cfg.start_url)
        if self.browser is None:
            self.browser = BrowserManager(
                headless=self.cfg.headless, timeout=self.cfg.page_timeout, max_retries=self.cfg.max_retries,
                user_agent=self.cfg.user_agent, wait_for_network_idle=self.cfg.wait_for_network_idle,
                request_delay=self.cfg.request_delay)
        try:
            self.writer = OutputWriter(self.db, self.job_id, self.cfg.output_dir, self.cfg.file_prefix,
                                       self.cfg.pages_per_file)
            self._load_profile()
            if mode == "retry_failed":
                await self._retry_failed()
                final = "stopped" if self.stop_requested else "completed"
                self._set_state(final, "retry pass finished")
            else:
                finished = await self._crawl()
                if self.stop_requested and not finished:
                    self._set_state("stopped", "stopped by user - resume to continue")
                elif finished:
                    self.writer.finalize()
                    batches = self.db.batches(self.job_id)
                    if batches:
                        self.db.update_job(self.job_id, current_output_file=batches[-1]["filename"])
                    self._set_state("completed", self.db.get_job(self.job_id).get("message") or "finished")
                    self.db.update_job(self.job_id, finished_at=_now())
        except BlockedError as exc:
            self._log("error", "blocked", f"access blocked: {exc}")
            self._set_state("blocked", f"Access blocked ({exc}). The extractor does not bypass CAPTCHAs, "
                                       "logins or access controls.")
        except WriterError as exc:
            self._log("error", "disk_error", str(exc))
            self._set_state("error", f"Output error: {exc}. Fix the problem and resume the job.")
        except ValueError as exc:
            self._log("error", "config_error", str(exc))
            self._set_state("error", str(exc))
        except FetchError as exc:
            self._log("error", "network_error", str(exc))
            self._set_state("error", f"Network error: {exc}. Resume the job to retry.")
        except asyncio.CancelledError:
            self._set_state("stopped", "cancelled")
            raise
        except Exception as exc:
            log.exception("job crashed")
            self._log("error", "crash", f"unexpected error: {exc!r}")
            self._set_state("error", f"Unexpected error: {exc!r}. Resume the job to continue.")
        finally:
            if self.writer:
                self.writer.close()
            if self._own_browser and self.browser is not None:
                await self.browser.close()
                self.browser = None

    # ------------------------------------------------------------------ crawl loop
    def _resume_point(self) -> tuple[str | None, int, int | None]:
        pages = self.db.get_pages(self.job_id)
        if not pages:
            return self.cfg.start_url, 0, None
        last = pages[-1]
        if last["status"] != "done":
            prev = pages[-2]["page_number"] if len(pages) > 1 else None
            return last["url"], last["seq"], prev
        return last["next_url"], last["seq"] + 1, last["page_number"]

    async def _crawl(self) -> bool:
        """Returns True when the crawl reached its natural end (last page or max pages)."""
        url, seq, prev_number = self._resume_point()
        if url is None:
            self._log("info", "already_complete", "job already reached the last page")
            return True
        if seq > 0:
            self._log("info", "resume", f"resuming at listing page sequence {seq + 1}", url=url)
        visited = {normalize_url(p["url"]) for p in self.db.get_pages(self.job_id) if p["status"] == "done"}
        seen_keys = {t for p in self.db.get_pages(self.job_id)
                     for t in (r["thread_key"] for r in self.db.page_threads(self.job_id, p["seq"]))}
        manual = self.cfg.mode == "manual"

        while url:
            if self.stop_requested:
                return False
            await self._wait_if_paused()
            if self.stop_requested:
                return False
            if self.cfg.max_pages and seq >= self.cfg.max_pages:
                self._log("info", "max_pages", f"reached the configured maximum of {self.cfg.max_pages} pages")
                self.db.update_job(self.job_id, message=f"stopped at the configured maximum of {self.cfg.max_pages} pages")
                return True
            if not in_scope(url, self.cfg.start_url, self.cfg.allowed_domains):
                self._log("warn", "out_of_scope", f"next page {url} is outside the forum scope; stopping")
                return True
            if self.cfg.respect_robots and not await self.browser.robots_allowed(url):
                raise BlockedError(f"robots.txt disallows {url}")

            self.current_page_url = url
            self.page_total = self.page_done = 0
            self.activity = f"Opening listing page #{seq + 1}: {url}"
            self.db.update_job(self.job_id, current_page_url=url)
            try:
                snap = await self.browser.fetch(url)
            except FetchError as exc:
                self.db.upsert_page(self.job_id, seq, url, None, "failed", error=str(exc))
                raise
            analysis = await asyncio.to_thread(
                analyze_html, snap.html, url, snap.final_url, self.cfg.start_url, self.profile, manual, visited,
                self.cfg.allowed_domains, snap.status)
            if analysis.page_type == "blocked":
                raise BlockedError(f"{analysis.blocked_reason} on {url}")
            if analysis.platform and self.spec is None:
                self.spec = analysis.spec
                self.db.update_job(self.job_id, platform=analysis.platform.label)
                self._log("info", "platform", f"detected platform {analysis.platform.label} "
                                              f"(confidence {analysis.platform.confidence:.2f})",
                          signals=analysis.platform.signals)
            elif seq == 0 and not analysis.platform:
                self.db.update_job(self.job_id, platform="Generic / custom HTML")

            # A thread URL was supplied instead of a listing page: extract just that thread.
            if seq == 0 and analysis.page_type == "thread":
                await self._single_thread(url, snap)
                return True

            links = analysis.threads.links
            if not links:
                if seq == 0:
                    raise ValueError(
                        "No discussion threads were found on the starting page "
                        f"(page type: {analysis.page_type}). Use Analyze/Preview to configure selectors.")
                self._log("info", "end", f"no threads found on {url}; treating it as the end of the forum")
                self.db.upsert_page(self.job_id, seq, url, None, "done", thread_count=0, next_url=None)
                return True

            number = analysis.page_number
            if number is None or (prev_number is not None and number <= prev_number):
                if number is not None:
                    self._log("warn", "page_number", f"page number {number} not increasing; using sequence")
                number = (prev_number + 1) if prev_number is not None else 1
            self.current_page_number = number
            self.db.update_job(self.job_id, current_page_number=number)

            new_keys = [l.key for l in links if l.key not in seen_keys]
            if seq > 0 and not new_keys and self._last_next_inferred(seq):
                self._log("info", "end", f"inferred page {url} returned no new threads; end of forum")
                self.db.upsert_page(self.job_id, seq, url, number, "done", thread_count=0, next_url=None)
                return True

            self.db.upsert_page(self.job_id, seq, url, number, "processing", thread_count=len(links))
            self.activity = f"Listing page {number}: found {len(links)} threads – reading them"
            self._log("info", "listing_page", f"listing page {number}: {len(links)} threads "
                                              f"({analysis.threads.method}, confidence {analysis.threads.confidence:.2f})",
                      url=url, next=analysis.pagination.next_url)
            if seq == 0 or self.profile is None:
                self._remember_listing_profile(analysis)

            rows = []
            for pos, link in enumerate(links):
                rows.append(self.db.add_thread(self.job_id, seq, number, url, pos, link.url, link.key, link.title))
                seen_keys.add(link.key)

            await self._process_page(seq, number, rows)
            if self.stop_requested:
                return False

            pag = analysis.pagination
            next_url, method = pag.next_url, pag.method
            if not next_url and pag.needs_confirmation:
                next_url = await self._ask_confirmation(url, pag)
                method = "confirmed by user" if next_url else "user chose to end"
                if self.stop_requested:
                    return False
            self.writer.page_done(seq, number)
            visited.add(normalize_url(url))
            visited.add(normalize_url(snap.final_url))
            if next_url and normalize_url(next_url) in visited:
                self._log("warn", "loop", f"next page {next_url} was already visited; stopping to avoid a loop")
                next_url = None
            self.db.upsert_page(self.job_id, seq, url, number, "done", next_url=next_url,
                                next_method=("inferred:" if pag.inferred else "") + method)
            self.db.update_job(self.job_id, current_output_file=self.writer.current_file())
            if not next_url:
                self._log("info", "end", f"last listing page reached ({method})")
                return True
            url, seq, prev_number = next_url, seq + 1, number
        return True

    def _last_next_inferred(self, seq: int) -> bool:
        prev = self.db.get_page(self.job_id, seq - 1)
        return bool(prev and (prev.get("next_method") or "").startswith("inferred:"))

    def _remember_listing_profile(self, analysis) -> None:
        if self.profile is not None and self.profile.source == "user":
            return
        sugg = suggest_profile(analysis, self.domain)
        if self.profile is not None:
            # keep learned post selectors from previous runs
            for f in ("post_container_selector", "first_post_selector", "post_body_selector"):
                if getattr(self.profile, f) and not getattr(sugg, f):
                    setattr(sugg, f, getattr(self.profile, f))
            if self.profile.exclude_selectors and not sugg.exclude_selectors:
                sugg.exclude_selectors = self.profile.exclude_selectors
        try:
            self.profile = self.profiles.save_auto(sugg)
        except OSError as exc:
            self._log("warn", "profile", f"could not save site profile: {exc}")
            self.profile = sugg

    async def _ask_confirmation(self, url: str, pag) -> str | None:
        cands = [c.to_dict() for c in pag.candidates]
        if self.cfg.on_unknown_pagination != "ask":
            self._log("warn", "pagination_unknown",
                      f"pagination could not be identified on {url}; stopping (candidates: "
                      f"{[c['url'] for c in cands[:5]]})")
            return None
        self.pending_confirmation = {"page_url": url, "candidates": cands}
        self._confirm = asyncio.get_running_loop().create_future()
        self.activity = "Waiting for you: choose the next page below"
        self._set_state("awaiting_confirmation", "Pagination could not be identified. Choose the next page link "
                                                 "or end the job.")
        self._log("warn", "pagination_unknown", "waiting for user to confirm the next page", candidates=cands[:10])
        try:
            choice = await self._confirm
        finally:
            self._confirm = None
            self.pending_confirmation = None
        if not self.stop_requested:
            self._set_state("running", "")
        if choice:
            choice = normalize_url(choice)
            if not in_scope(choice, self.cfg.start_url, self.cfg.allowed_domains):
                self._log("warn", "out_of_scope", f"confirmed URL {choice} is outside the forum scope")
                return None
        return choice

    # ------------------------------------------------------------------ threads
    async def _process_page(self, seq: int, number: int, rows: list[dict]) -> None:
        order = sorted(rows, key=lambda r: r["position"])
        ready: dict[int, Outcome] = {}
        pointer = [0]
        sem = asyncio.Semaphore(self.cfg.max_concurrency)

        async def handle(row: dict) -> None:
            async with sem:
                await self._wait_if_paused()
                if self.stop_requested:
                    return
                self.in_flight[row["id"]] = row.get("title") or row["thread_url"]
                self._update_activity(number)
                try:
                    outcome = await self._thread_outcome(row)
                finally:
                    self.in_flight.pop(row["id"], None)
            ready[row["id"]] = outcome
            self.page_done += 1
            self._update_activity(number)
            async with self._write_lock:
                self._flush(seq, number, order, ready, pointer)

        todo = [r for r in order if r["status"] == "pending"]
        self.page_total = len(order)
        self.page_done = len(order) - len(todo)
        await asyncio.gather(*(handle(r) for r in todo))
        async with self._write_lock:
            self._flush(seq, number, order, ready, pointer)

    def _update_activity(self, number: int | None) -> None:
        if self.state != "running":
            return
        titles = list(self.in_flight.values())
        now = f" – reading: {titles[0][:80]}" + (f" (+{len(titles) - 1} more)" if len(titles) > 1 else "") if titles else ""
        self.activity = f"Listing page {number}: thread {self.page_done}/{self.page_total} done{now}"

    def _flush(self, seq: int, number: int, order: list[dict], ready: dict[int, Outcome], pointer: list[int]) -> None:
        """Write finished threads in listing order (a slow thread holds back the ones after it)."""
        while pointer[0] < len(order):
            row = order[pointer[0]]
            if row["status"] != "pending":
                pointer[0] += 1
                continue
            outcome = ready.get(row["id"])
            if outcome is None:
                return
            self._record(seq, number, row, outcome)
            row["status"] = outcome.status
            pointer[0] += 1

    def _record(self, seq: int, number: int | None, row: dict, o: Outcome) -> None:
        if o.status == "done":
            h = hashlib.sha256(re.sub(r"\s+", " ", o.text).strip().encode("utf-8")).hexdigest()
            if self.cfg.skip_duplicates and self.db.hash_done_in_job(self.job_id, h, row["id"]):
                self.db.update_thread(row["id"], status="duplicate", content_hash=h, confidence=o.confidence,
                                      error="identical original post already written")
                self._log("info", "duplicate", f"duplicate content: {row['thread_url']}")
                return
            path = self.writer.write(seq, number, row["title"] or o.title, row["thread_url"], o.text)
            self.db.update_thread(row["id"], status="done", confidence=o.confidence, method=o.method,
                                  output_file=str(path), content_hash=h, error="")
            self.db.update_job(self.job_id, current_output_file=str(path), last_confidence=o.confidence)
            self.last_confidence = o.confidence
            self.done_this_run += 1
            self._log("info", "extracted", f"saved: {row['title'] or o.title} ({(o.confidence or 0) * 100:.0f}%)",
                      url=row["thread_url"])
        elif o.status == "failed":
            self.db.update_thread(row["id"], status="failed", confidence=o.confidence, method=o.method,
                                  error=o.error, retry_count=(row.get("retry_count") or 0) + 1)
            self._log("error", "extraction_failed", f"{row['thread_url']}: {o.error}", url=row["thread_url"])
        else:
            self.db.update_thread(row["id"], status=o.status, error=o.error)
            self._log("info", o.status, f"{o.status}: {row['thread_url']} {o.error}".strip())

    async def _thread_outcome(self, row: dict) -> Outcome:
        url = row["thread_url"]
        if self.cfg.skip_duplicates and (self.db.key_done_in_job(self.job_id, row["thread_key"], row["id"])
                                         or self.db.key_done_elsewhere(self.job_id, row["thread_key"])):
            return Outcome("duplicate", error="thread already processed (see its first occurrence)")
        if not in_scope(url, self.cfg.start_url, self.cfg.allowed_domains):
            return Outcome("skipped", error="outside forum scope")
        if self.cfg.respect_robots and not await self.browser.robots_allowed(url):
            return Outcome("skipped", error="robots.txt disallows this URL")
        try:
            snap = await self.browser.fetch(url)
        except FetchError as exc:
            return Outcome("failed", error=f"network: {exc}")
        return await self._extract_snapshot(snap, row.get("title") or "")

    async def _extract_snapshot(self, snap: PageSnapshot, title: str) -> Outcome:
        soup = await asyncio.to_thread(soup_from_html, snap.html)
        blocked = detect_blocking(snap.html, soup, snap.status)
        spec = self.spec
        if spec is None:
            match, _ = detect_platform(soup, snap.html, snap.final_url)
            spec = get_platform(match.name) if match else None
        if self.classifier is not None:
            self.classifier.title = title
        res = await asyncio.to_thread(
            extract_original_post, soup, snap.final_url, spec, self.profile, self.cfg.confidence_threshold,
            self.cfg.exclude_quotes, self.cfg.mode == "manual", self.classifier, title)
        if res.ok:
            self._maybe_learn(res)
            return Outcome("done", text=res.text, confidence=res.confidence, method=res.method, title=res.title)
        if blocked in HARD_BLOCKS:  # challenge page and no post content: stop, never bypass
            raise BlockedError(f"{blocked} on {snap.url}")
        if blocked:  # login wall / 401 / 403 and no post content visible
            return Outcome("skipped", error=f"access restricted: {blocked}", title=res.title)
        if snap.status and snap.status >= 400:
            return Outcome("failed", error=f"HTTP {snap.status}", title=res.title)
        return Outcome("failed", confidence=res.confidence or None, method=res.method, error=res.error,
                       title=res.title)

    def _maybe_learn(self, res) -> None:
        """Learn post selectors from a confident generic extraction (used for later threads)."""
        if self.cfg.mode == "manual" or res.method not in ("semantic", "structural"):
            return
        if res.confidence < max(0.7, self.cfg.confidence_threshold):
            return
        if self.profile is not None and self.profile.source == "user":
            return
        if self.profile is not None and self.profile.has_post_selectors() and \
                self.profile.post_container_selector == res.container_selector:
            return
        prof = self.profile or SiteProfile(domain=self.domain)
        prof.post_container_selector = res.container_selector
        prof.post_body_selector = res.body_selector
        prof.exclude_selectors = list(res.exclude_selectors)
        prof.confidence = round(res.confidence, 3)
        prof.notes = "post selectors learned automatically from a high-confidence extraction"
        try:
            self.profile = self.profiles.save_auto(prof)
        except OSError:
            self.profile = prof
        self._log("info", "profile_learned", f"learned post selectors: {res.container_selector!r} / "
                                             f"{res.body_selector!r}")

    async def _single_thread(self, url: str, snap: PageSnapshot) -> None:
        self._log("info", "single_thread", "starting URL is a discussion thread; extracting it only")
        canon = canonical_thread_url(snap.final_url or url)
        self.db.upsert_page(self.job_id, 0, url, None, "processing", thread_count=1)
        row = self.db.add_thread(self.job_id, 0, None, url, 0, canon, thread_key(canon), "")
        outcome = await self._extract_snapshot(snap, "")
        if not row.get("title") and outcome.title:
            self.db.update_thread(row["id"], title=outcome.title)
            row["title"] = outcome.title
        self._record(0, None, row, outcome)
        self.writer.page_done(0, None)
        self.db.upsert_page(self.job_id, 0, url, None, "done", next_url=None, next_method="single thread")

    async def _retry_failed(self) -> None:
        failed = self.db.failed_threads(self.job_id)
        self._log("info", "retry", f"retrying {len(failed)} failed threads")
        job = self.db.get_job(self.job_id) or {}
        if self.spec is None:
            from .platforms import PLATFORMS
            self.spec = next((p for p in PLATFORMS if p.label == job.get("platform")), None)
        for row in failed:
            if self.stop_requested:
                return
            await self._wait_if_paused()
            outcome = await self._thread_outcome({**row, "status": "pending"})
            self._record(row["page_seq"], row["page_number"], row, outcome)
            if outcome.status == "done":
                self.writer.sync()


class JobManager:
    """Owns the running jobs of the application."""

    def __init__(self, db: Database, profiles: ProfileStore):
        self.db = db
        self.profiles = profiles
        self.runners: dict[str, JobRunner] = {}
        self.tasks: dict[str, asyncio.Task] = {}

    def create(self, config: JobConfig) -> str:
        config.validate()
        return self.db.create_job(config.start_url, config.to_dict())

    def is_running(self, job_id: str) -> bool:
        t = self.tasks.get(job_id)
        return t is not None and not t.done()

    def start(self, job_id: str, mode: str = "crawl", browser: BrowserManager | None = None) -> JobRunner:
        if self.is_running(job_id):
            raise RuntimeError("job is already running")
        job = self.db.get_job(job_id)
        if not job:
            raise KeyError(job_id)
        cfg = JobConfig.from_dict(job["config"])
        runner = JobRunner(self.db, self.profiles, job_id, cfg, browser)
        self.runners[job_id] = runner
        self.tasks[job_id] = asyncio.create_task(runner.run(mode))
        return runner

    def runner(self, job_id: str) -> JobRunner | None:
        return self.runners.get(job_id)

    async def delete(self, job_id: str, delete_files: bool = False) -> list[str]:
        """Stop the job if needed and remove its records (and optionally its output files)."""
        if self.is_running(job_id):
            self.runners[job_id].stop()
            try:
                await asyncio.wait_for(self.tasks[job_id], timeout=60)
            except (asyncio.TimeoutError, Exception):
                self.tasks[job_id].cancel()
        removed = []
        if delete_files:
            for b in self.db.batches(job_id):
                f = Path(b["filename"])
                try:
                    if f.exists():
                        f.unlink()
                        removed.append(str(f))
                except OSError:
                    pass
        self.db.delete_job(job_id)
        self.runners.pop(job_id, None)
        self.tasks.pop(job_id, None)
        return removed

    async def shutdown(self) -> None:
        for r in self.runners.values():
            r.stop()
        for t in self.tasks.values():
            if not t.done():
                try:
                    await asyncio.wait_for(t, timeout=20)
                except (asyncio.TimeoutError, Exception):
                    t.cancel()

    def status(self, job_id: str) -> dict:
        job = self.db.get_job(job_id)
        if not job:
            raise KeyError(job_id)
        counts = self.db.thread_counts(job_id)
        runner = self.runners.get(job_id)
        running = self.is_running(job_id)
        pages = self.db.get_pages(job_id)
        errors = self.db.events(job_id, level="error", limit=15, newest_first=True)
        events = self.db.events(job_id, limit=40, newest_first=True)
        live = runner is not None and running
        elapsed = time.monotonic() - runner.started_monotonic if live and runner.started_monotonic else None
        rate = (runner.done_this_run / (elapsed / 60)) if live and elapsed and elapsed > 5 else None
        max_pages = job["config"].get("max_pages")
        return {
            "id": job_id,
            "status": runner.state if (runner and running) else job["status"],
            "running": running,
            "message": job["message"],
            "start_url": job["start_url"],
            "platform": job["platform"],
            "config": job["config"],
            "current_page_url": job["current_page_url"],
            "current_page_number": job["current_page_number"],
            "listing_pages_done": sum(1 for p in pages if p["status"] == "done"),
            "threads_discovered": counts.get("discovered", 0),
            "threads_completed": counts.get("done", 0),
            "threads_skipped": counts.get("skipped", 0),
            "threads_failed": counts.get("failed", 0),
            "threads_duplicate": counts.get("duplicate", 0),
            "threads_pending": counts.get("pending", 0),
            "current_output_file": job["current_output_file"],
            "last_confidence": job["last_confidence"],
            "avg_confidence": self.db.avg_confidence(job_id),
            "recent_errors": [{"ts": e["ts"], "message": e["message"]} for e in errors],
            "recent_events": [{"ts": e["ts"], "level": e["level"], "event": e["event"], "message": e["message"]}
                              for e in events],
            "activity": runner.activity if live else None,
            "page_progress": {"done": runner.page_done, "total": runner.page_total} if live else None,
            "elapsed_seconds": round(elapsed) if elapsed else None,
            "threads_per_minute": round(rate, 1) if rate else None,
            "max_pages": max_pages,
            "pending_confirmation": runner.pending_confirmation if runner else None,
            "output_files": [b["filename"] for b in self.db.batches(job_id)],
            "output_dir": str(Path(job["config"].get("output_dir", "output")).expanduser().resolve()),
            "created_at": job["created_at"], "updated_at": job["updated_at"],
        }


def _now() -> str:
    from .storage import now_iso
    return now_iso()
