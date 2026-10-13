"""Discovery of discussion-thread links on forum listing pages."""
from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Tag

from .config import SiteProfile
from .domutil import ancestors, group_selector, in_chrome, norm_space, safe_select, signature, text_of
from .platforms import PlatformSpec
from .urls import absolutize, canonical_thread_url, in_scope, listing_base, normalize_url, thread_key, url_template

THREAD_URL_HINT = re.compile(
    r"(thread|topic|showthread|viewtopic|discussion|/t/|/d/\d|/posts?/|/p/\d|comments|question|/post\.|"
    r"/conversation)",
    re.I,
)
NEG_URL_HINT = re.compile(
    r"(member|/users?/|/u/|profile|login|log-in|logout|register|signup|sign-up|search|/tags?/|/help|faq|"
    r"account|/forums?/?$|viewforum|forumdisplay|categor|/c/|/category/|whats-new|/rss|\.rss|feed|"
    r"[?&](start|page|pg|p|offset)=\d|/page[-/]\d|/p\d+/?$|/latest$|/unread$|/newest$|lastpost|goto=)",
    re.I,
)


SECONDARY_HINT = re.compile(r"(side|related|trending|popular|similar|suggest|recent|latest|widget|teaser|footer)",
                            re.I)


def _row_extra(a: Tag) -> float:
    """1.0 when the link sits in a row that also carries other info (author, stats, dates)."""
    title_len = len(text_of(a))
    node = a
    for _ in range(4):
        parent = node.parent
        if parent is None or parent.name in ("body", "html", "ul", "ol", "table", "tbody"):
            break
        if len(parent.find_all("a", href=True)) > 6:
            break
        node = parent
    extra = len(text_of(node)) - title_len
    return 1.0 if extra >= 12 else extra / 12


@dataclass
class ThreadLink:
    url: str
    title: str
    key: str


@dataclass
class ThreadDiscovery:
    links: list[ThreadLink] = field(default_factory=list)
    selector: str = ""
    container_selector: str = ""
    method: str = "none"
    confidence: float = 0.0
    alternatives: list[dict] = field(default_factory=list)

    def to_dict(self, limit: int = 200) -> dict:
        return {
            "count": len(self.links),
            "selector": self.selector,
            "container_selector": self.container_selector,
            "method": self.method,
            "confidence": round(self.confidence, 3),
            "items": [{"title": l.title, "url": l.url} for l in self.links[:limit]],
            "alternatives": self.alternatives[:8],
        }


def _title_for(a: Tag, title_selector: str = "") -> str:
    if title_selector:
        t = a.select_one(title_selector)
        if t is not None:
            s = text_of(t)
            if s:
                return s
    s = text_of(a)
    if not s:
        s = norm_space(a.get("title") or a.get("aria-label") or "")
    return s


def _links_from_anchors(anchors: list[Tag], base_url: str, start_url: str, allowed: list[str],
                        title_selector: str = "") -> list[ThreadLink]:
    out: list[ThreadLink] = []
    seen: set[str] = set()
    for a in anchors:
        if a.name != "a":
            inner = a.find("a", href=True)
            if inner is None:
                continue
            a = inner
        url = absolutize(a.get("href", ""), base_url)
        if not url or not in_scope(url, start_url, allowed):
            continue
        canon = canonical_thread_url(url)
        key = thread_key(canon)
        if key in seen:
            continue
        title = _title_for(a, title_selector)
        if not title:
            continue
        seen.add(key)
        out.append(ThreadLink(url=canon, title=title, key=key))
    return out


def discover_threads(
    soup: BeautifulSoup,
    base_url: str,
    start_url: str,
    spec: PlatformSpec | None = None,
    profile: SiteProfile | None = None,
    allowed_domains: list[str] | None = None,
    manual_only: bool = False,
) -> ThreadDiscovery:
    allowed = allowed_domains or []
    # 1. user / saved profile
    if profile and profile.thread_link_selector:
        root_nodes = safe_select(soup, profile.listing_container_selector) if profile.listing_container_selector else [soup]
        anchors: list[Tag] = []
        for root in root_nodes or [soup]:
            anchors.extend(safe_select(root, profile.thread_link_selector))
        links = _links_from_anchors(anchors, base_url, start_url, allowed, profile.thread_title_selector)
        if links or manual_only:
            conf = 0.95 if profile.source == "user" else 0.85
            return ThreadDiscovery(links, profile.thread_link_selector, profile.listing_container_selector,
                                   "profile", conf if links else 0.0)
    if manual_only:
        return ThreadDiscovery(method="profile")
    # 2. platform selectors
    if spec:
        for sel in spec.thread_links:
            anchors = safe_select(soup, sel)
            links = _links_from_anchors(anchors, base_url, start_url, allowed, spec.thread_title)
            if links:
                generic = _generic(soup, base_url, start_url, allowed)
                gen_keys = {l.key for l in generic.links}
                agree = len(gen_keys & {l.key for l in links}) / max(1, len(links))
                conf = 0.85 + 0.1 * agree
                return ThreadDiscovery(links, sel, "", f"platform:{spec.name}", min(conf, 0.98),
                                       alternatives=generic.alternatives)
    # 3. generic DOM analysis
    return _generic(soup, base_url, start_url, allowed)


def _ancestor_sig(a: Tag, depth: int = 3) -> str:
    sigs = [signature(a)]
    for i, anc in enumerate(ancestors(a)):
        if i >= depth or anc.name in ("body", "html"):
            break
        sigs.append(signature(anc))
    return " < ".join(sigs)


def _generic(soup: BeautifulSoup, base_url: str, start_url: str, allowed: list[str]) -> ThreadDiscovery:
    current_base = listing_base(base_url)
    current_tmpl = url_template(normalize_url(base_url))
    groups: dict[tuple[str, str], list[tuple[Tag, str]]] = defaultdict(list)
    for a in soup.find_all("a", href=True):
        url = absolutize(a["href"], base_url)
        if not url or not in_scope(url, start_url, allowed):
            continue
        if in_chrome(a):
            continue
        nurl = normalize_url(url)
        if listing_base(nurl) == current_base:
            continue  # pagination / self links
        groups[(_ancestor_sig(a), url_template(nurl))].append((a, nurl))
    all_groups = dict(groups)

    scored = []
    for (sig, tmpl), members in groups.items():
        urls = {u for _, u in members}
        n = len(urls)
        if n < 2:
            continue
        texts = [text_of(a) for a, _ in members]
        avg_len = sum(len(t) for t in texts) / len(texts)
        kw = sum(1 for u in urls if THREAD_URL_HINT.search(u)) / n
        neg = sum(1 for u in urls if NEG_URL_HINT.search(u)) / n
        numeric = sum(1 for t in texts if re.fullmatch(r"[\d\s.,:/-]*", t) or len(t) <= 3) / len(texts)
        heading = sum(1 for a, _ in members if a.find_parent(re.compile(r"^h[1-6]$")) is not None
                      or any("title" in c.lower() for c in (a.get("class") or []))
                      or any("title" in c.lower() for c in ((a.parent.get("class") if a.parent else None) or []))
                      ) / len(members)
        same_tmpl = 1.0 if tmpl == current_tmpl else 0.0
        words = sum(1 for t in texts if len(t.split()) >= 2) / len(texts)
        secondary = sum(1 for a, _ in members if any(SECONDARY_HINT.search(t) for anc in list(ancestors(a))[:6]
                                                     for t in (anc.get("class") or []) + [anc.get("id") or ""])) / len(members)
        row_extra = sum(_row_extra(a) for a, _ in members) / len(members)
        score = (
            0.30 * min(1.0, math.log2(n + 1) / math.log2(21))
            + 0.20 * min(1.0, avg_len / 30)
            + 0.15 * words
            + 0.20 * kw
            + 0.15 * heading
            + 0.15 * row_extra
            - 0.30 * secondary
            - 0.50 * neg
            - 0.60 * numeric
            - 0.60 * same_tmpl
        )
        scored.append((score, sig, tmpl, members))
    scored.sort(key=lambda x: -x[0])
    alternatives = []
    for score, sig, tmpl, members in scored[:8]:
        alternatives.append({
            "score": round(score, 3),
            "count": len({u for _, u in members}),
            "url_pattern": tmpl,
            "selector": group_selector(soup, [a for a, _ in members]),
            "sample": [text_of(a)[:80] for a, _ in members[:3]],
        })
    if not scored or scored[0][0] <= 0.2:
        return ThreadDiscovery(method="generic", confidence=0.0, alternatives=alternatives)
    score, sig, tmpl, members = scored[0]
    # Merge other groups with the same URL template and similar anchor structure (e.g. sticky sections).
    anchors = [a for a, _ in members]
    anchor_sig = sig.split(" < ")[0]
    for (sig2, tmpl2), m2 in all_groups.items():
        if sig2 == sig or tmpl2 != tmpl or sig2.split(" < ")[0] != anchor_sig:
            continue
        # same link markup & URL shape in a differently-styled row (sticky / announcement / pinned)
        if all(len(text_of(a)) >= 3 and not NEG_URL_HINT.search(u) for a, u in m2):
            anchors.extend(a for a, _ in m2)
    order = {id(a): i for i, a in enumerate(soup.find_all("a"))}
    anchors.sort(key=lambda a: order.get(id(a), 0))
    links = _links_from_anchors(anchors, base_url, start_url, allowed)
    margin = score - (scored[1][0] if len(scored) > 1 else 0.0)
    conf = max(0.0, min(0.9, score + 0.25 * min(margin, 0.4)))
    selector = group_selector(soup, anchors)
    return ThreadDiscovery(links, selector, "", "generic", conf, alternatives)
