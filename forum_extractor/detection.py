"""Site detection pipeline: platform fingerprinting and page-type classification."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

from .config import SiteProfile
from .domutil import safe_select, safe_select_one, text_of
from .extraction import find_post_groups
from .listing import ThreadDiscovery, discover_threads
from .pagination import PaginationResult, discover_pagination
from .platforms import PLATFORMS, PlatformSpec, get_platform
from .textutil import soup_from_html

CAPTCHA_MARKERS = [
    (re.compile(r"cf-challenge|challenge-platform|cf_chl_|Just a moment\.\.\.|Checking your browser", re.I),
     "Cloudflare challenge"),
    (re.compile(r"g-recaptcha|recaptcha/api\.js|hcaptcha\.com|h-captcha|turnstile", re.I), "CAPTCHA"),
    (re.compile(r"Access denied|You don't have permission to access|Request blocked", re.I), "access denied"),
]
LOGIN_MARKERS = re.compile(
    r"(you must be (logged|signed) in|you must log in|please (log|sign) in to (view|continue|read)|"
    r"login required|you do not have permission to view this|members only)",
    re.I,
)


@dataclass
class PlatformMatch:
    name: str
    label: str
    score: float
    confidence: float
    signals: list[str]


@dataclass
class PageAnalysis:
    url: str
    final_url: str
    title: str
    canonical: str
    platform: PlatformMatch | None
    page_type: str  # listing | thread | blocked | other
    page_type_confidence: float
    page_number: int | None
    threads: ThreadDiscovery
    pagination: PaginationResult
    blocked_reason: str = ""
    notes: list[str] = field(default_factory=list)
    platform_scores: list[dict] = field(default_factory=list)

    @property
    def spec(self) -> PlatformSpec | None:
        return get_platform(self.platform.name) if self.platform else None

    def to_dict(self) -> dict:
        return {
            "url": self.url, "final_url": self.final_url, "title": self.title, "canonical": self.canonical,
            "platform": self.platform.name if self.platform else "generic",
            "platform_label": self.platform.label if self.platform else "Generic / custom HTML",
            "platform_confidence": round(self.platform.confidence, 3) if self.platform else 0.0,
            "platform_signals": self.platform.signals if self.platform else [],
            "platform_scores": self.platform_scores,
            "page_type": self.page_type, "page_type_confidence": round(self.page_type_confidence, 3),
            "page_number": self.page_number, "blocked_reason": self.blocked_reason, "notes": self.notes,
            "threads": self.threads.to_dict(), "pagination": self.pagination.to_dict(),
        }


def detect_blocking(html: str, soup: BeautifulSoup, status: int | None) -> str:
    """Return a reason if the page is a CAPTCHA / login wall / access denial; never try to bypass it."""
    head = html[:200000]
    visible = text_of(soup.body)[:5000] if soup.body else ""
    for rx, reason in CAPTCHA_MARKERS:
        if rx.search(head) and (len(visible) < 2500 or reason != "access denied"):
            if reason == "CAPTCHA" and len(visible) > 2500:
                continue  # a CAPTCHA widget on a normal content page (e.g. a reply form)
            return reason
    if status in (401, 403):
        return f"HTTP {status} (access restricted)"
    if LOGIN_MARKERS.search(visible) and len(visible) < 3000:
        return "login required"
    return ""


def detect_platform(soup: BeautifulSoup, html: str, url: str) -> tuple[PlatformMatch | None, list[dict]]:
    gen = safe_select_one(soup, "meta[name='generator']")
    generator = (gen.get("content") or "") if gen else ""
    scores = []
    for spec in PLATFORMS:
        score = 0.0
        hits = []
        for sig in spec.signals:
            hit = False
            if sig.kind == "css":
                hit = bool(safe_select(soup, sig.value))
            elif sig.kind == "generator":
                hit = bool(re.search(sig.value, generator, re.I))
            elif sig.kind == "html":
                hit = bool(re.search(sig.value, html[:400000]))
            elif sig.kind == "url":
                hit = bool(re.search(sig.value, url, re.I))
            if hit:
                score += sig.weight
                hits.append(f"{sig.kind}:{sig.value}")
        scores.append((score, spec, hits))
    scores.sort(key=lambda s: -s[0])
    table = [{"platform": s.name, "score": sc, "signals": h} for sc, s, h in scores if sc > 0]
    best_score, spec, hits = scores[0]
    runner_up = scores[1][0] if len(scores) > 1 else 0
    if best_score < 3:
        return None, table
    conf = min(1.0, best_score / 7.0) * (0.85 if best_score - runner_up < 2 else 1.0)
    return PlatformMatch(spec.name, spec.label, best_score, round(conf, 3), hits), table


def analyze_html(
    html: str,
    url: str,
    final_url: str | None = None,
    start_url: str | None = None,
    profile: SiteProfile | None = None,
    manual_only: bool = False,
    visited: set[str] | None = None,
    allowed_domains: list[str] | None = None,
    status: int | None = 200,
    soup: BeautifulSoup | None = None,
) -> PageAnalysis:
    final_url = final_url or url
    start_url = start_url or url
    soup = soup or soup_from_html(html)
    title = text_of(soup.title) if soup.title else ""
    canon_el = safe_select_one(soup, "link[rel='canonical']")
    canonical = canon_el.get("href", "") if canon_el else ""

    blocked = detect_blocking(html, soup, status)
    platform, table = detect_platform(soup, html, final_url)
    if profile and profile.source == "user" and profile.platform not in ("", "custom", "generic") and platform is None:
        spec = get_platform(profile.platform)
        if spec:
            platform = PlatformMatch(spec.name, spec.label, 0, 0.5, ["from saved site profile"])
    spec = get_platform(platform.name) if platform else None

    threads = discover_threads(soup, final_url, start_url, spec, profile, allowed_domains, manual_only)
    thread_urls = {l.url for l in threads.links}

    # page type
    listing_score = threads.confidence if len(threads.links) >= 2 else threads.confidence * 0.5
    groups = find_post_groups(soup, limit=1)
    thread_score = groups[0][0] if groups else 0.0
    notes = []
    if spec:
        if any(safe_select(soup, m) for m in spec.listing_markers):
            listing_score = max(listing_score, 0.9)
            notes.append(f"{spec.label} listing markup present")
        if any(safe_select(soup, m) for m in spec.thread_markers):
            # thread pages often embed "similar threads" lists; post markup wins over listing markup
            thread_score = max(thread_score, 0.95)
            notes.append(f"{spec.label} post markup present")
    if profile and profile.has_post_selectors():
        if safe_select(soup, profile.first_post_selector or profile.post_container_selector):
            thread_score = max(thread_score, 0.85)
    if blocked:
        page_type, ptc = "blocked", 1.0
    elif listing_score >= 0.4 and listing_score > thread_score:
        page_type, ptc = "listing", listing_score
    elif thread_score >= 0.4:
        page_type, ptc = "thread", thread_score
    elif listing_score >= 0.3:
        page_type, ptc = "listing", listing_score
    else:
        page_type, ptc = "other", max(listing_score, thread_score)

    pagination = discover_pagination(
        soup, final_url, start_url, spec, profile, visited, allowed_domains, thread_urls,
        has_threads=bool(threads.links),
    )
    return PageAnalysis(
        url=url, final_url=final_url, title=title, canonical=canonical, platform=platform,
        page_type=page_type, page_type_confidence=ptc, page_number=pagination.current_page,
        threads=threads, pagination=pagination, blocked_reason=blocked, notes=notes, platform_scores=table,
    )


def suggest_profile(analysis: PageAnalysis, domain: str) -> SiteProfile:
    """Generate a site profile from an automatic analysis (listing-side selectors)."""
    spec = analysis.spec
    p = SiteProfile(domain=domain, platform=analysis.platform.name if analysis.platform else "custom")
    p.thread_link_selector = analysis.threads.selector
    p.listing_container_selector = analysis.threads.container_selector
    if spec:
        p.thread_title_selector = spec.thread_title
        p.next_page_selector = ", ".join(s for s in spec.next_page if not s.startswith("link"))
        p.post_container_selector = spec.post_containers[0] if spec.post_containers else ""
        p.first_post_selector = spec.first_post[0] if spec.first_post else ""
        p.post_body_selector = spec.post_body[0] if spec.post_body else ""
        from .platforms import COMMON_EXCLUDE
        p.exclude_selectors = list(dict.fromkeys(spec.exclude + spec.quote_exclude + COMMON_EXCLUDE))
    p.confidence = round(analysis.threads.confidence, 3)
    return p
