"""High-level operations shared by the web UI and the CLI: analyse a URL, test one thread."""
from __future__ import annotations

import asyncio
from dataclasses import replace

from .browser import BlockedError, BrowserManager
from .classifier import make_classifier
from .config import JobConfig, SiteProfile
from .detection import HARD_BLOCKS, analyze_html, detect_blocking, detect_platform, suggest_profile
from .extraction import extract_original_post
from .platforms import get_platform
from .profiles import ProfileStore
from .textutil import soup_from_html
from .urls import canonical_thread_url, domain_key


def effective_profile(store: ProfileStore, url: str, overrides: dict | None) -> SiteProfile | None:
    domain = domain_key(url)
    prof = store.get(domain)
    if overrides:
        base = prof.to_dict() if prof else {"domain": domain}
        base.update({k: v for k, v in overrides.items() if v is not None})
        base["domain"] = domain
        base["source"] = "user"
        prof = SiteProfile.from_dict(base)
    return prof


async def test_thread(browser: BrowserManager, url: str, profile: SiteProfile | None, cfg: JobConfig,
                      title: str = "", platform_name: str | None = None, data_dir=None) -> dict:
    from .urls import is_reddit
    if cfg.source == "reddit" or is_reddit(url):
        from . import reddit as rd
        t = rd.parse_target(url)
        if t is not None and t.post_id:
            client = rd.RedditClient(browser, t, rd.load_credentials(data_dir) if data_dir else None, cfg.user_agent)
            if client.api_mode:
                posts, _ = rd.parse_listing(await client.get(client.post_url()), t.base)
            else:
                page = await browser.new_page()
                try:
                    await rd.open_feed(page, rd.page_url(t), cfg.page_timeout * 1000)
                    posts = (await rd.read_page_posts(page, t.base))[:1]
                finally:
                    await page.close()
            if posts:
                p = posts[0]
                return {"ok": True, "url": p.url, "title": p.title, "text": rd.record_text(p), "confidence": 1.0,
                        "method": "reddit-json", "warnings": [] if p.is_self else ["image/link post - skipped in jobs"],
                        "candidates": [], "container_html": "", "post_count": 1, "platform": "reddit"}
    snap = await browser.fetch(url)
    soup = await asyncio.to_thread(soup_from_html, snap.html)
    blocked = detect_blocking(snap.html, soup, snap.status)
    spec = get_platform(platform_name) if platform_name else None
    if spec is None:
        match, _ = detect_platform(soup, snap.html, snap.final_url)
        spec = get_platform(match.name) if match else None
    classifier = make_classifier(cfg.ai_classifier, cfg.ai_model, cfg.ai_endpoint)
    res = await asyncio.to_thread(
        extract_original_post, soup, snap.final_url, spec, profile, cfg.confidence_threshold, cfg.exclude_quotes,
        cfg.mode == "manual", classifier, title)
    out = res.to_dict()
    if not res.ok and blocked:
        out["error"] = f"access restricted ({blocked}) and no post content was found. {res.error}"
    out["platform"] = spec.name if spec else "generic"
    out["http_status"] = snap.status
    return out


async def analyze_url(browser: BrowserManager, url: str, store: ProfileStore, cfg: JobConfig,
                      overrides: dict | None = None, preview_thread: str | None = None) -> dict:
    """Analyse a starting URL and (for listing pages) test-extract one thread."""
    from .urls import is_reddit
    if cfg.source == "reddit" or is_reddit(url):
        return await analyze_reddit(browser, url, cfg, store.dir.parent)
    profile = effective_profile(store, url, overrides)
    snap = await browser.fetch(url)
    manual = cfg.mode == "manual"
    analysis = await asyncio.to_thread(
        analyze_html, snap.html, url, snap.final_url, cfg.start_url or url, profile, manual, set(),
        cfg.allowed_domains, snap.status)
    result = {"analysis": analysis.to_dict(), "http_status": snap.status}
    domain = domain_key(url)
    suggestion = suggest_profile(analysis, domain)
    if profile is not None:
        # keep whatever the user / saved profile already specifies
        merged = suggestion.to_dict()
        for k, v in profile.to_dict().items():
            if v not in ("", [], None, 0.0):
                merged[k] = v
        suggestion = SiteProfile.from_dict(merged)
    result["saved_profile"] = store.get(domain).to_dict() if store.get(domain) else None
    platform_name = analysis.platform.name if analysis.platform else None

    if analysis.page_type == "blocked":
        result["preview"] = {"ok": False, "error": f"access blocked: {analysis.blocked_reason}"}
    elif analysis.page_type == "thread" and not preview_thread:
        result["preview"] = await test_thread(browser, canonical_thread_url(snap.final_url), profile, cfg,
                                              platform_name=platform_name)
    else:
        target = preview_thread or (analysis.threads.links[0].url if analysis.threads.links else None)
        title = ""
        if not preview_thread and analysis.threads.links:
            title = analysis.threads.links[0].title
        if target:
            result["preview"] = await test_thread(browser, target, profile, cfg, title, platform_name)
        else:
            result["preview"] = {"ok": False, "error": "no discussion threads were found on this page; "
                                                       "edit the thread-link selector", "text": "", "candidates": []}
    prev = result.get("preview") or {}
    if prev.get("ok") and prev.get("method") in ("semantic", "structural") and not suggestion.post_container_selector:
        suggestion = replace(suggestion, post_container_selector=prev.get("container_selector", ""),
                             post_body_selector=prev.get("body_selector", ""),
                             exclude_selectors=prev.get("exclude_selectors", []))
    result["suggested_profile"] = suggestion.to_dict()
    return result


async def analyze_reddit(browser: BrowserManager, url: str, cfg: JobConfig, data_dir=None) -> dict:
    """Preview of a subreddit: the first 100 posts of the chosen list and the first text post."""
    from . import reddit as rd
    t = rd.parse_target(url)
    if t is None:
        raise ValueError("That doesn't look like a subreddit. Use r/name or a link like https://www.reddit.com/r/name/")
    client = rd.RedditClient(browser, t, rd.load_credentials(data_dir) if data_dir else None, cfg.user_agent)
    if not client.api_mode:
        page = await browser.new_page()
        try:
            await rd.open_feed(page, rd.page_url(t), cfg.page_timeout * 1000)
            posts = await rd.read_page_posts(page, t.base)
        finally:
            await page.close()
        return _reddit_result(url, t, posts, more=bool(posts) and not t.post_id, api_mode=False)
    try:
        data = await client.get(client.post_url() if t.post_id else client.listing_url())
    except rd.RedditAuthError as exc:
        raise BlockedError(str(exc)) from exc
    except BlockedError as exc:
        if client.api_mode:
            raise
        raise BlockedError(f"{exc}. Reddit refuses anonymous automated access from your connection - add your free "
                           "Reddit API key under 'Reddit API access' in the Reddit tab") from exc
    posts, after = rd.parse_listing(data, t.base)
    return _reddit_result(url, t, posts, more=bool(after), api_mode=True)


def _reddit_result(url, t, posts, more: bool, api_mode: bool) -> dict:
    from . import reddit as rd
    wanted = [p for p in posts if rd.wanted(p)]
    first = wanted[0] if wanted else None
    analysis = {
        "url": url, "final_url": url, "title": f"r/{t.subreddit}", "canonical": "",
        "platform": "reddit", "platform_label": "Reddit", "platform_confidence": 1.0, "platform_signals": [],
        "platform_scores": [], "page_type": "thread" if t.post_id else "listing", "page_type_confidence": 1.0,
        "page_number": None, "blocked_reason": "",
        "notes": [f"{len(posts)} posts read, {len(wanted)} are text posts"],
        "threads": {"count": len(wanted), "selector": "", "container_selector": "", "method": "reddit-json",
                    "confidence": 1.0, "items": [{"title": p.title, "url": p.url} for p in wanted],
                    "alternatives": []},
        "pagination": {"next_url": "more" if more else None,
                       "method": "Reddit API" if api_mode else "scrolling the Reddit page",
                       "confidence": 1.0, "current_page": None, "last_page": None, "is_last": not more,
                       "inferred": False, "needs_confirmation": False, "selector": "", "candidates": []},
    }
    preview = ({"ok": True, "url": first.url, "title": first.title, "text": rd.record_text(first), "confidence": 1.0,
                "method": "reddit-json", "warnings": [], "candidates": [], "container_html": "", "post_count": 1}
               if first else {"ok": False, "error": "no text posts in this list", "text": "", "candidates": []})
    return {"analysis": analysis, "preview": preview, "http_status": 200,
            "suggested_profile": {"domain": "reddit.com", "platform": "reddit"}, "saved_profile": None,
            "reddit": {"subreddit": t.subreddit, "sort": t.sort, "posts_in_first_batch": len(posts),
                       "api_mode": api_mode}}
