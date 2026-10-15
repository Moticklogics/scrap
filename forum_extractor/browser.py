"""Playwright browser management: navigation with timeouts, retries, crash recovery and rate limiting."""
from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import time
from dataclasses import dataclass
from urllib import robotparser
from urllib.parse import urlsplit

log = logging.getLogger("forum_extractor.browser")

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524}


class FetchError(Exception):
    """The page could not be loaded after all retries."""


class BlockedError(Exception):
    """The site requires a CAPTCHA, login or otherwise denies access. Never bypassed."""


@dataclass
class PageSnapshot:
    url: str
    final_url: str
    status: int | None
    html: str
    title: str
    elapsed: float


class RateLimiter:
    """Ensures at least `delay` seconds between the start of consecutive requests (all workers)."""

    def __init__(self, delay: float):
        self.delay = max(0.0, delay)
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._last + self.delay - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()


def _chromium_candidates() -> list[str | None]:
    cands: list[str | None] = [None]
    env = os.environ.get("FORUM_EXTRACTOR_CHROMIUM")
    if env:
        cands.insert(0, env)
    for p in ("/opt/pw-browsers/chromium",):
        if os.path.exists(p):
            cands.append(p)
    return cands


class BrowserManager:
    def __init__(
        self,
        headless: bool = True,
        timeout: float = 30.0,
        max_retries: int = 3,
        user_agent: str = "",
        wait_for_network_idle: bool = True,
        request_delay: float = 0.0,
        block_resources: bool = True,
    ):
        self.headless = headless
        self.timeout = timeout
        self.max_retries = max_retries
        self.user_agent = user_agent
        self.wait_for_network_idle = wait_for_network_idle
        self.block_resources = block_resources
        self.limiter = RateLimiter(request_delay)
        self._pw = None
        self._browser = None
        self._context = None
        self._start_lock = asyncio.Lock()
        self._robots: dict[str, robotparser.RobotFileParser | None] = {}

    async def start(self) -> None:
        async with self._start_lock:
            if self._context is not None and self._browser is not None and self._browser.is_connected():
                return
            from playwright.async_api import async_playwright

            if self._pw is None:
                self._pw = await async_playwright().start()
            last_exc = None
            for exe in _chromium_candidates():
                try:
                    kw = {"headless": self.headless}
                    if exe:
                        kw["executable_path"] = exe
                    self._browser = await self._pw.chromium.launch(**kw)
                    break
                except Exception as exc:  # try the next candidate executable
                    last_exc = exc
            if self._browser is None:
                if isinstance(last_exc, NotImplementedError):
                    raise RuntimeError(
                        "Chromium could not be started because of the asyncio event loop (Windows). Start the "
                        "app with 'python -m forum_extractor serve'.") from last_exc
                raise RuntimeError(
                    "Could not launch Chromium. Run 'python -m playwright install chromium' "
                    f"or set FORUM_EXTRACTOR_CHROMIUM. Last error: {last_exc}")
            ctx_kw = {"java_script_enabled": True, "ignore_https_errors": False}
            if self.user_agent:
                ctx_kw["user_agent"] = self.user_agent
            self._context = await self._browser.new_context(**ctx_kw)
            self._context.set_default_timeout(self.timeout * 1000)
            if self.block_resources:
                await self._context.route("**/*", self._route)

    async def _route(self, route):
        if route.request.resource_type in ("image", "media", "font"):
            await route.abort()
        else:
            await route.continue_()

    async def close(self) -> None:
        for obj in (self._context, self._browser):
            try:
                if obj is not None:
                    await obj.close()
            except Exception:
                pass
        self._context = self._browser = None
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception:
                pass
            self._pw = None

    async def _restart(self) -> None:
        log.warning("restarting browser")
        for obj in (self._context, self._browser):
            try:
                if obj is not None:
                    await obj.close()
            except Exception:
                pass
        self._context = self._browser = None
        await self.start()

    async def fetch(self, url: str) -> PageSnapshot:
        """Load a page and return its rendered HTML. Retries transient failures with exponential backoff."""
        from .urls import to_old_reddit
        url = to_old_reddit(url)
        await self.start()
        attempt = 0
        last_err = ""
        while attempt <= self.max_retries:
            attempt += 1
            await self.limiter.wait()
            page = None
            t0 = time.monotonic()
            try:
                if self._browser is None or not self._browser.is_connected():
                    await self._restart()
                page = await self._context.new_page()
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
                status = resp.status if resp else None
                if status in RETRYABLE_STATUS:
                    retry_after = 0.0
                    if resp is not None and status == 429:
                        try:
                            retry_after = float((await resp.all_headers()).get("retry-after", "0"))
                        except Exception:
                            retry_after = 0.0
                    last_err = f"HTTP {status}"
                    await self._backoff(attempt, minimum=min(retry_after, 120))
                    continue
                if self.wait_for_network_idle:
                    try:
                        await page.wait_for_load_state("networkidle", timeout=min(8000, self.timeout * 1000))
                    except Exception:
                        pass  # long-polling sites never go idle; the DOM is usually ready anyway
                html = await page.content()
                title = await page.title()
                return PageSnapshot(url, page.url, status, html, title, time.monotonic() - t0)
            except Exception as exc:
                msg = str(exc).splitlines()[0][:300]
                last_err = msg
                log.warning("fetch failed (%s/%s) %s: %s", attempt, self.max_retries + 1, url, msg)
                if "Target" in msg and "closed" in msg or "Browser has been closed" in msg or "disconnected" in msg:
                    try:
                        await self._restart()
                    except Exception as rexc:
                        last_err = f"browser restart failed: {rexc}"
                await self._backoff(attempt)
            finally:
                if page is not None:
                    try:
                        await page.close()
                    except Exception:
                        pass
        raise FetchError(f"{url}: {last_err} (after {self.max_retries + 1} attempts)")

    async def fetch_json(self, url: str, user_agent: str = ""):
        """GET a JSON document (no page rendering). Retries 429/5xx and network errors with backoff."""
        import json as _json
        await self.start()
        headers = {"Accept": "application/json"}
        if user_agent:
            headers["User-Agent"] = user_agent
        attempt = 0
        last_err = ""
        while attempt <= self.max_retries:
            attempt += 1
            await self.limiter.wait()
            try:
                resp = await self._context.request.get(url, headers=headers, timeout=self.timeout * 1000,
                                                       max_redirects=5)
                status = resp.status
                body = await resp.text()
            except Exception as exc:
                last_err = str(exc).splitlines()[0][:300]
                log.warning("json fetch failed (%s/%s) %s: %s", attempt, self.max_retries + 1, url, last_err)
                await self._backoff(attempt)
                continue
            if status == 429 or status >= 500:
                wait = 0.0
                try:
                    h = resp.headers
                    wait = float(h.get("retry-after") or h.get("x-ratelimit-reset") or 0)
                except (TypeError, ValueError):
                    wait = 0.0
                last_err = f"HTTP {status}" + (" (rate limited)" if status == 429 else "")
                await self._backoff(attempt, minimum=min(max(wait, 10.0 if status == 429 else 0.0), 120.0))
                continue
            if status in (401, 403):
                reason = ""
                try:
                    reason = (_json.loads(body) or {}).get("reason") or ""
                except ValueError:
                    pass
                if re.search(r"blocked by network security|whoa there", body, re.I):
                    raise BlockedError("Reddit blocked this connection (network security). Wait a while, use a "
                                       "longer delay, and try again")
                raise BlockedError(f"access denied (HTTP {status}{', ' + reason if reason else ''})")
            if status == 404:
                raise FetchError(f"{url}: not found (HTTP 404) - check the name; private or banned communities "
                                 "cannot be read")
            try:
                return _json.loads(body)
            except ValueError:
                if re.search(r"blocked by network security|whoa there|captcha", body, re.I):
                    raise BlockedError("Reddit returned a block/CAPTCHA page instead of data")
                last_err = f"HTTP {status}: response was not JSON"
                await self._backoff(attempt)
        raise FetchError(f"{url}: {last_err} (after {self.max_retries + 1} attempts)")

    async def _backoff(self, attempt: int, minimum: float = 0.0) -> None:
        delay = max(minimum, min(30.0, 1.0 * (2 ** (attempt - 1)))) + random.uniform(0, 0.3)
        await asyncio.sleep(delay)

    async def robots_allowed(self, url: str, user_agent: str = "*") -> bool:
        from .urls import to_old_reddit
        parts = urlsplit(to_old_reddit(url))
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            rp: robotparser.RobotFileParser | None = None
            try:
                await self.start()
                resp = await self._context.request.get(origin + "/robots.txt", timeout=15000)
                if resp.ok:
                    rp = robotparser.RobotFileParser()
                    rp.parse((await resp.text()).splitlines())
            except Exception as exc:
                log.info("robots.txt unavailable for %s: %s", origin, exc)
            self._robots[origin] = rp
        rp = self._robots[origin]
        return True if rp is None else rp.can_fetch(user_agent, url)
