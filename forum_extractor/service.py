"""High-level operations shared by the web UI and the CLI: analyse a URL, test one thread."""
from __future__ import annotations

import asyncio
from dataclasses import replace

from .browser import BrowserManager
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
                      title: str = "", platform_name: str | None = None) -> dict:
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
