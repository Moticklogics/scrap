"""Original-post extraction.

Detection hierarchy (each produces a candidate; every candidate is validated and scored):
  1. site profile (user-confirmed or learned)        prior 0.95 / 0.85
  2. known selectors for the detected platform        prior 0.90
  3. semantic markup (schema.org, <article>, post ids) prior 0.75-0.80
  4. structural analysis of repeated DOM elements     prior 0.60-0.75
  5. content-density fallback (single post pages)     prior 0.45
  6. optional local AI classifier for ambiguous pages (chooses among candidates, never writes text)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from bs4 import BeautifulSoup, Tag

from .config import SiteProfile
from .domutil import (
    similar_groups,
    POST_HINT, ancestors, css_simple, group_selector, in_chrome, in_quote, is_ancestor, is_meta, is_meta_or_quote,
    link_density, norm_space,
    relative_selector, safe_select, safe_select_one, signature, text_of, tokens,
)
from .pagination import infer_page_number
from .platforms import COMMON_EXCLUDE, COMMON_QUOTE_EXCLUDE, PlatformSpec
from .textutil import html_to_text

BODY_HINT = re.compile(
    r"(^|[-_])(body|content|message|text|cooked|postbody|entry|prose|markdown|usercontent|bbwrapper|messagetext|"
    r"posttext|postcontent|articlebody|post-?body|post-?text|post-?content|comment-?body)([-_]|$)",
    re.I,
)
POST_ID = re.compile(r"^(post|p|msg|message|comment|reply|entry|postid)[-_]?\d+$", re.I)
PREVIEW_HINT = re.compile(r"(preview|snippet|excerpt|search-?result|searchresult|teaser|summary)", re.I)
NAV_WORDS = {
    "home", "forums", "forum", "login", "log", "in", "register", "sign", "up", "reply", "quote", "share", "report",
    "like", "likes", "next", "prev", "previous", "page", "pages", "members", "search", "menu", "new", "posts",
    "threads", "help", "contact", "us", "terms", "rules", "privacy", "policy", "top", "back", "of", "watch",
    "bookmark", "more", "options", "go", "to", "first", "last", "unread", "the", "and", "a", "you", "must",
}
SEMANTIC_SELECTORS = [
    ("[itemtype*='DiscussionForumPosting']", 0.82),
    ("[itemtype*='schema.org/Comment']", 0.8),
    ("article", 0.75),
]
_NUMBER_ATTRS = ("data-post-number", "data-number", "data-index", "data-post-index", "data-position", "data-postnumber")


@dataclass
class Candidate:
    method: str
    container: Tag
    body: Tag
    others: list[Tag]
    prior: float
    exclude: list[str]
    meta_filter: bool
    container_selector: str = ""
    body_selector: str = ""
    reason: str = ""
    text: str = ""
    confidence: float = 0.0
    rejected: str = ""
    warnings: list[str] = field(default_factory=list)
    quote_filter: bool = False

    def to_dict(self) -> dict:
        return {
            "method": self.method,
            "confidence": round(self.confidence, 3),
            "container_selector": self.container_selector,
            "body_selector": self.body_selector,
            "rejected": self.rejected,
            "warnings": self.warnings,
            "reason": self.reason,
            "preview": self.text[:300],
        }


@dataclass
class ExtractionResult:
    ok: bool
    url: str
    text: str = ""
    title: str = ""
    confidence: float = 0.0
    method: str = ""
    container_selector: str = ""
    body_selector: str = ""
    exclude_selectors: list[str] = field(default_factory=list)
    container_html: str = ""
    warnings: list[str] = field(default_factory=list)
    error: str = ""
    candidates: list[dict] = field(default_factory=list)
    ai_reason: str = ""
    post_count: int = 0

    def to_dict(self) -> dict:
        return {
            "ok": self.ok, "url": self.url, "title": self.title, "text": self.text,
            "confidence": round(self.confidence, 3), "method": self.method,
            "container_selector": self.container_selector, "body_selector": self.body_selector,
            "exclude_selectors": self.exclude_selectors, "container_html": self.container_html,
            "warnings": self.warnings, "error": self.error, "candidates": self.candidates,
            "ai_reason": self.ai_reason, "post_count": self.post_count,
        }


# --------------------------------------------------------------------------- helpers

def _top_meta(el: Tag) -> list[Tag]:
    out = []
    for d in el.find_all(True):
        if is_meta(d) and not any(is_meta(a) for a in ancestors(d) if a is not el and is_ancestor(el, a)):
            out.append(d)
    return out


def content_len(el: Tag) -> int:
    total = len(text_of(el))
    for m in _top_meta(el):
        total -= len(text_of(m))
    return max(0, total)


def order_first(members: list[Tag]) -> Tag:
    """Pick the original post among post containers using explicit numbering, else document order."""
    for m in members:
        for attr in _NUMBER_ATTRS:
            v = m.get(attr)
            if v is None:
                continue
            want = "0" if "index" in attr else "1"
            if str(v).strip() == want:
                return m
        mid = str(m.get("id") or "")
        if re.fullmatch(r"(post|p)[-_]?1", mid, re.I):
            return m
    return members[0]


def find_body_generic(container: Tag) -> tuple[Tag, bool]:
    """Locate the post-body element inside a post container. Returns (body, needs_meta_filter)."""
    total = content_len(container) or 1
    hinted = []
    for d in container.find_all(True):
        if d.name in ("a", "span", "img", "time", "br"):
            continue
        hint = any(BODY_HINT.search(t) for t in tokens(d)) or d.get("itemprop") in ("text", "articleBody")
        if not hint or is_meta(d) or in_quote(d, stop=container) or in_chrome(d):
            continue
        cl = content_len(d)
        if cl >= 0.5 * total:
            hinted.append((cl, d))
    if hinted:
        best_len = max(cl for cl, _ in hinted)
        good = [d for cl, d in hinted if cl >= 0.9 * best_len]
        # deepest element that still holds (almost) all of the text
        deepest = [d for d in good if not any(o is not d and is_ancestor(d, o) for o in good)]
        body = deepest[0]
        return body, bool(_top_meta(body))
    cur = container
    while True:
        cur_len = content_len(cur)
        nxt = None
        for child in cur.find_all(True, recursive=False):
            if is_meta(child) or child.name in ("a", "span", "br", "img"):
                continue
            if content_len(child) >= 0.85 * cur_len and cur_len > 0:
                nxt = child
                break
        if nxt is None:
            break
        cur = nxt
    return cur, cur is container or bool(_top_meta(cur))


def find_post_groups(soup: BeautifulSoup, limit: int = 5) -> list[tuple[float, list[Tag]]]:
    """Find groups of repeated sibling elements that look like posts, best first."""
    results = []
    roots = soup.find_all(True)
    if isinstance(soup, Tag) and not isinstance(soup, BeautifulSoup):
        roots = [soup, *roots]
    for parent in roots:
        kids = [c for c in parent.find_all(True, recursive=False)]
        if len(kids) < 2:
            continue
        kids = [k for k in kids if k.name not in (
            "p", "span", "a", "br", "strong", "em", "b", "i", "img", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th",
            "option", "script", "style", "link", "meta", "input", "label", "dt", "dd", "hr")]
        for members in similar_groups(kids):
            if len(members) < 2:
                continue
            if in_chrome(parent):
                continue
            lens = [content_len(m) for m in members]
            srt = sorted(lens)
            median = srt[len(srt) // 2]
            if median < 25:
                continue
            ld = sum(link_density(m) for m in members) / len(members)
            meta = sum(1 for m in members if m.find("time") is not None or any(is_meta(d) for d in m.find_all(True))) / len(members)
            postish = sum(1 for m in members if any(POST_HINT.search(t) for t in tokens(m))
                          or POST_ID.match(str(m.get("id") or ""))) / len(members)
            # listing rows: most of the row's text is a single link
            listing_like = 0.0
            for m in members:
                anchors = m.find_all("a")
                if anchors:
                    longest = max(len(text_of(a)) for a in anchors)
                    if longest >= 0.4 * max(1, len(text_of(m))) and len(text_of(m)) < 300:
                        listing_like += 1
            listing_like /= len(members)
            blocky = sum(1 for m in members if m.find(["p", "div", "br", "blockquote", "ul", "td"]) is not None) / len(members)
            score = (
                0.30 * min(1.0, median / 300)
                + 0.20 * (1 - ld)
                + 0.20 * meta
                + 0.15 * postish
                + 0.05 * min(1.0, len(members) / 10)
                + 0.10 * blocky
                - 0.45 * listing_like
            )
            results.append((score, members))
    results.sort(key=lambda r: -r[0])
    # drop groups nested inside a better group's members
    final: list[tuple[float, list[Tag]]] = []
    for score, members in results:
        if any(any(is_ancestor(m2, members[0]) for m2 in mem2) for _, mem2 in final):
            continue
        final.append((score, members))
        if len(final) >= limit:
            break
    return final


def _semantic_groups(soup: BeautifulSoup) -> list[tuple[float, list[Tag], str]]:
    out = []
    for sel, prior in SEMANTIC_SELECTORS:
        els = [e for e in safe_select(soup, sel) if not in_chrome(e) and content_len(e) >= 15]
        els = [e for e in els if not any(o is not e and is_ancestor(o, e) for o in els)]
        if els:
            out.append((prior, els, sel))
    # ids like post-123 / class tokens like "post" or "message"
    hits = []
    for el in soup.find_all(True):
        if el.name in ("a", "span", "img", "body", "html", "form", "button", "i", "b", "strong", "em"):
            continue
        if POST_ID.match(str(el.get("id") or "")) or any(POST_HINT.search(t) for t in (el.get("class") or [])):
            if in_chrome(el) or content_len(el) < 15:
                continue
            hits.append(el)
    for els in similar_groups(hits):
        els = [e for e in els if not any(o is not e and is_ancestor(o, e) for o in els)]
        if els:
            out.append((0.72, els, signature(els[0])))
    return out


def _density_fallback(soup: BeautifulSoup) -> Tag | None:
    best, best_len = None, 0
    root = soup.find("main") or soup.body or soup
    for el in root.find_all(["div", "section", "article", "td"]):
        if in_chrome(el) or link_density(el) > 0.3:
            continue
        # prefer elements whose text is mostly in their own paragraphs (not big wrappers)
        cl = content_len(el)
        child_max = max([content_len(c) for c in el.find_all(True, recursive=False)] or [0])
        if cl > best_len and child_max < 0.85 * cl:
            best, best_len = el, cl
    return best


def _nav_like(text: str) -> bool:
    words = re.findall(r"[^\W\d_]+", text.lower())
    if not words:
        return True
    navf = sum(1 for w in words if w in NAV_WORDS) / len(words)
    return len(text) < 200 and navf >= 0.5


def _strip_title_line(text: str, title: str) -> str:
    """Generic containers often include the thread/post title as a heading; drop it if it leads the text."""
    lines = text.split("\n")
    if not lines:
        return text
    first = norm_space(lines[0]).lower()
    t = norm_space(title).lower()
    if t and first in (t, "re: " + t, "re:" + t) and len(lines) > 1:
        return "\n".join(lines[1:]).lstrip("\n")
    return text


def _reply_snippet(other: Tag) -> str:
    t = html_to_text(other, COMMON_QUOTE_EXCLUDE, exclude_pred=is_meta_or_quote)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) < 25:
        return ""
    return t[-40:]


# --------------------------------------------------------------------------- candidates

def _gather(
    soup: BeautifulSoup, spec: PlatformSpec | None, profile: SiteProfile | None, exclude_quotes: bool,
    manual_only: bool,
) -> list[Candidate]:
    cands: list[Candidate] = []
    quote_ex = COMMON_QUOTE_EXCLUDE if exclude_quotes else []

    if profile and profile.has_post_selectors():
        posts = safe_select(soup, profile.post_container_selector) if profile.post_container_selector else []
        first = safe_select_one(soup, profile.first_post_selector) if profile.first_post_selector else None
        if first is None and posts:
            first = order_first(posts)
        if first is not None:
            body = safe_select_one(first, profile.post_body_selector) if profile.post_body_selector else None
            body = body or first
            prior = 0.95 if profile.source == "user" else 0.85
            cands.append(Candidate(
                "profile", first, body, [p for p in posts if p is not first], prior,
                list(profile.exclude_selectors), profile.source != "user" and not profile.post_body_selector,
                profile.first_post_selector or profile.post_container_selector, profile.post_body_selector,
                quote_filter=exclude_quotes and profile.source != "user", reason=f"{profile.source} site profile"))
    if manual_only:
        return cands

    if spec:
        for sel in spec.post_containers:
            posts = safe_select(soup, sel)
            if not posts:
                continue
            first = None
            fsel = sel
            for fs in spec.first_post:
                first = safe_select_one(soup, fs)
                if first is not None:
                    fsel = fs
                    break
            first = first or order_first(posts)
            body, bsel = None, ""
            for bs in spec.post_body:
                body = safe_select_one(first, bs)
                if body is not None:
                    bsel = bs
                    break
            if body is None:
                continue
            excl = spec.exclude + COMMON_EXCLUDE + ((spec.quote_exclude or []) if exclude_quotes else [])
            cands.append(Candidate(
                f"platform:{spec.name}", first, body, [p for p in posts if p is not first], 0.9, excl, False,
                fsel, bsel, reason=f"known {spec.label} markup ({sel})"))
            break

    for prior, members, sel in _semantic_groups(soup):
        first = order_first(members)
        body, meta = find_body_generic(first)
        cands.append(Candidate(
            "semantic", first, body, [m for m in members if m is not first], prior,
            COMMON_EXCLUDE + quote_ex, meta, group_selector(soup, members), relative_selector(first, body),
            quote_filter=exclude_quotes, reason=f"semantic markup '{sel}' ({len(members)} posts)"))

    for score, members in find_post_groups(soup, limit=3):
        first = order_first(members)
        body, meta = find_body_generic(first)
        prior = max(0.4, min(0.78, 0.45 + score * 0.45))
        cands.append(Candidate(
            "structural", first, body, [m for m in members if m is not first], prior,
            COMMON_EXCLUDE + quote_ex, meta, group_selector(soup, members), relative_selector(first, body),
            quote_filter=exclude_quotes, reason=f"{len(members)} repeated post-like elements (score {score:.2f}); first in thread order"))

    dens = _density_fallback(soup)
    if dens is not None:
        dbody, _ = find_body_generic(dens)
        cands.append(Candidate("density", dens, dbody, [], 0.45, COMMON_EXCLUDE + quote_ex, True,
                               group_selector(soup, [dens]), relative_selector(dens, dbody), quote_filter=exclude_quotes,
                               reason="largest dense text block"))
    return cands


def _validate(c: Candidate, soup: BeautifulSoup, page_text_len: int, title: str = "") -> None:
    pred: Callable[[Tag], bool] | None = None
    if c.meta_filter and c.quote_filter:
        pred = is_meta_or_quote
    elif c.meta_filter:
        pred = is_meta
    elif c.quote_filter:
        from .domutil import is_quote
        pred = is_quote
    try:
        c.text = html_to_text(c.body, c.exclude, exclude_pred=pred)
    except Exception as exc:  # pragma: no cover - defensive
        c.rejected = f"text conversion failed: {exc}"
        return
    if title and c.method in ("semantic", "structural", "density", "profile"):
        c.text = _strip_title_line(c.text, title)
    conf = c.prior
    if not c.text.strip():
        c.rejected = "empty post body"
        return
    if c.body.name in ("html", "body") or c.container.name in ("html", "body"):
        c.rejected = "selected element is the entire page"
        return
    if in_chrome(c.container):
        c.rejected = "element is inside navigation / sidebar / footer"
        return
    if in_quote(c.container) or in_quote(c.body, stop=c.container):
        c.rejected = "element is a quotation, not a post"
        return
    for a in [c.container, *ancestors(c.container)]:
        if any(PREVIEW_HINT.search(t) for t in tokens(a)):
            c.rejected = "element is a preview / search snippet"
            return
    for o in c.others:
        if o is c.body or is_ancestor(c.body, o) or is_ancestor(c.container, o):
            c.rejected = "element contains other posts (replies)"
            return
    for score, members in find_post_groups(c.body, limit=1):
        with_meta = sum(1 for m in members if any(is_meta(d) for d in m.find_all(True)))
        if score >= 0.55 and len(members) >= 2 and with_meta == len(members):
            c.rejected = f"element contains {len(members)} separate posts"
            return
    flat = re.sub(r"\s+", " ", c.text)
    for o in c.others[:15]:
        snip = _reply_snippet(o)
        if snip and snip in flat:
            c.rejected = "element contains reply text"
            return
    if _nav_like(c.text):
        c.rejected = "text consists of navigation labels"
        return
    ld = link_density(c.body)
    if ld > 0.8 and len(c.text) < 500:
        c.rejected = "text is mostly links"
        return
    if ld > 0.5:
        conf *= 0.8
        c.warnings.append("high link density")
    if len(c.text) < 20:
        conf *= 0.9
        c.warnings.append("very short post")
    if title and norm_space(c.text).lower() == norm_space(title).lower():
        conf *= 0.4
        c.warnings.append("extracted text equals the thread title")
    if not c.others:
        conf *= 0.97
        c.warnings.append("only one post on page; reply separation could not be cross-checked")
    if page_text_len and len(flat) > 0.85 * page_text_len:
        conf *= 0.85
        c.warnings.append("post text is almost the whole page")
    c.confidence = conf


def extract_original_post(
    soup: BeautifulSoup,
    url: str,
    spec: PlatformSpec | None = None,
    profile: SiteProfile | None = None,
    threshold: float = 0.6,
    exclude_quotes: bool = True,
    manual_only: bool = False,
    classifier=None,
    title: str = "",
) -> ExtractionResult:
    res = ExtractionResult(ok=False, url=url, title=title or page_title(soup))
    if re.search(r"(/search[/?]|[?&](q|query|keywords|search)=)", url, re.I):
        res.error = "URL looks like a search page, not a discussion thread"
        return res
    page_no = infer_page_number(soup, url, spec)
    if page_no is not None and page_no > 1:
        res.error = f"page {page_no} of the thread was loaded; the original post is on page 1"
        return res
    if spec and spec.listing_markers and any(safe_select(soup, m) for m in spec.listing_markers) \
            and not any(safe_select(soup, m) for m in spec.thread_markers):
        res.error = "page is a forum listing, not a discussion thread"
        return res

    body_el = soup.body or soup
    page_text_len = len(text_of(body_el))
    cands = _gather(soup, spec, profile, exclude_quotes, manual_only)
    for c in cands:
        _validate(c, soup, page_text_len, res.title)

    valid = [c for c in cands if not c.rejected]
    # agreement bonus: independent methods that extract the same text
    for c in valid:
        same = {o.method.split(":")[0] for o in valid if o.text == c.text}
        if len(same) > 1:
            c.confidence = min(0.99, c.confidence + 0.04 * (len(same) - 1))
            c.reason += f"; agrees with {len(same) - 1} other method(s)"
    valid.sort(key=lambda c: -c.confidence)
    res.candidates = [c.to_dict() for c in sorted(cands, key=lambda c: -c.confidence)]

    best = valid[0] if valid else None
    if best is not None and best.confidence < threshold and classifier is not None and len(valid) >= 2:
        try:
            choice = classifier.choose(valid)
        except Exception as exc:  # classifier problems never break extraction
            choice = None
            res.warnings.append(f"AI classifier failed: {exc}")
        if choice is not None:
            idx, reason = choice
            if 0 <= idx < len(valid):
                best = valid[idx]
                best.confidence = max(best.confidence, min(0.75, threshold))
                best.method = f"ai+{best.method}"
                res.ai_reason = reason

    if best is None:
        reasons = "; ".join(sorted({c.rejected for c in cands if c.rejected})) or "no post containers found"
        res.error = f"no valid original-post candidate ({reasons}). Configure selectors manually for this forum."
        return res

    res.text = best.text
    res.confidence = best.confidence
    res.method = best.method
    res.container_selector = best.container_selector
    res.body_selector = best.body_selector
    res.exclude_selectors = best.exclude
    res.warnings += best.warnings
    res.post_count = 1 + len(best.others)
    html = str(best.container)
    res.container_html = html[:6000] + ("…" if len(html) > 6000 else "")
    if best.confidence < threshold:
        res.error = (f"low confidence {best.confidence:.2f} < threshold {threshold:.2f} "
                     f"(best method: {best.method}). Configure selectors manually for this forum.")
        return res
    res.ok = True
    return res


def page_title(soup: BeautifulSoup) -> str:
    og = safe_select_one(soup, "meta[property='og:title']")
    if og is not None and og.get("content"):
        return norm_space(og.get("content"))
    for h in soup.find_all(["h1", "h2"]):
        if not in_chrome(h):
            t = text_of(h)
            if t:
                return re.sub(r"^(thread|topic|discussion)\s*:\s*", "", t, flags=re.I)
    if soup.title:
        t = text_of(soup.title)
        return re.split(r"\s+[|\-–—·]\s+", t)[0].strip()
    return ""


def learn_profile_fields(result: ExtractionResult, soup: BeautifulSoup) -> dict:
    """Profile selectors derived from a successful generic extraction."""
    return {
        "post_container_selector": result.container_selector,
        "post_body_selector": result.body_selector,
        "exclude_selectors": result.exclude_selectors,
    }
