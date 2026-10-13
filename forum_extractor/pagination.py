"""Discovery of listing-page pagination (next page, current page number, last page)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlsplit

from bs4 import BeautifulSoup, Tag

from .config import SiteProfile
from .domutil import ancestors, classes, safe_select, text_of, tokens
from .platforms import PlatformSpec
from .urls import absolutize, get_query_param, in_scope, listing_base, normalize_url, set_query_param

NEXT_TEXT = re.compile(
    r"^\s*(next(\s+page)?|next\s+(results|topics|threads)|older(\s+(posts|topics|threads))?|load more|"
    r"weiter|nächste(\s+seite)?|suivante?|page suivante|siguiente|pr[oó]xim[ao]|avanti|successiv[ao]|volgende|"
    r"следующая|далее|вперед|下一页|下一頁|次へ|次のページ|다음|sonraki|następna|dalej|nästa|neste|næste|seuraava)"
    r"\s*[›»>→⟩❯]{0,2}\s*$",
    re.I,
)
_ARROWS_ONLY = re.compile(r"^\s*[›»>→⟩❯]{1,2}\s*$")
LAST_TEXT = re.compile(r"(last|letzte|dernière|última|ultima|последняя|末页|最後|[»›>]\|)", re.I)
PREV_TEXT = re.compile(r"(prev|previous|back|zurück|précédente?|anterior|precedente|назад|上一页|前へ|[‹«<←])", re.I)
PAGINATION_HINT = re.compile(r"(pag|pager|pagenav|pages|page-nav|pagenumbers|pagelinks)", re.I)
CURRENT_HINT = re.compile(r"^(active|current|selected|currentpage|highlight|is-current|is-active|"
                          r"pageNav-page--current|current-page|on)$", re.I)

_URL_PAGE_PATTERNS = [
    re.compile(r"/page[-/]?(\d+)/?(?:$|[?#&])", re.I),
    re.compile(r"[?&](?:page|pg|pagenumber|pagenum)=(\d+)", re.I),
    re.compile(r"/p(\d+)/?$", re.I),
    re.compile(r"[?&]p=(\d+)", re.I),
]


@dataclass
class PageCandidate:
    url: str
    text: str
    reason: str
    score: float

    def to_dict(self) -> dict:
        return {"url": self.url, "text": self.text, "reason": self.reason, "score": round(self.score, 3)}


@dataclass
class PaginationResult:
    next_url: str | None = None
    method: str = "none"
    confidence: float = 0.0
    current_page: int | None = None
    last_page: int | None = None
    is_last: bool = False
    inferred: bool = False
    needs_confirmation: bool = False
    selector: str = ""
    candidates: list[PageCandidate] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "next_url": self.next_url, "method": self.method, "confidence": round(self.confidence, 3),
            "current_page": self.current_page, "last_page": self.last_page, "is_last": self.is_last,
            "inferred": self.inferred, "needs_confirmation": self.needs_confirmation, "selector": self.selector,
            "candidates": [c.to_dict() for c in self.candidates[:40]],
        }


def _int(s: str) -> int | None:
    s = s.strip().replace(",", "").replace(".", "")
    return int(s) if s.isdigit() and len(s) < 7 else None


def pagination_containers(soup: BeautifulSoup) -> list[Tag]:
    out = []
    for el in soup.find_all(True):
        if el.name in ("html", "body", "a", "span", "li"):
            continue
        toks = tokens(el)
        hinted = any(PAGINATION_HINT.search(t) for t in toks) or (el.get("aria-label") or "").lower().startswith("pag")
        if not hinted and el.get("role") != "navigation" and el.name != "nav":
            continue
        nums = [a for a in el.find_all("a", href=True) if _int(text_of(a)) is not None]
        if len(nums) >= 1 or el.find(attrs={"rel": "next"}):
            if not any(o is a for o in out for a in [el, *ancestors(el)]):
                out.append(el)
    return out


def _current_from_dom(soup: BeautifulSoup, spec: PlatformSpec | None) -> int | None:
    if spec:
        for sel in spec.current_page:
            for el in safe_select(soup, sel):
                n = _int(text_of(el))
                if n is not None:
                    return n
    for cont in pagination_containers(soup):
        for el in cont.find_all(True):
            if el.get("aria-current") == "page" or any(CURRENT_HINT.match(c) for c in classes(el)):
                n = _int(text_of(el))
                if n is not None:
                    return n
        # a bare number that is not a link (e.g. <strong>2</strong> or <span>2</span>)
        for el in cont.find_all(["strong", "b", "span", "em"]):
            if el.find_parent("a") is None and el.find("a") is None:
                n = _int(text_of(el))
                if n is not None:
                    return n
    return None


def _current_from_url(url: str, spec: PlatformSpec | None, soup: BeautifulSoup | None) -> int | None:
    if spec and spec.page_param:
        key, first = spec.page_param
        v = get_query_param(url, key)
        if v is not None and v.isdigit():
            return int(v) - first + 1
        return 1
    for rx in _URL_PAGE_PATTERNS:
        m = rx.search(url)
        if m:
            return int(m.group(1))
    start = get_query_param(url, "start") or get_query_param(url, "offset")
    if start is not None and start.isdigit() and soup is not None:
        per = _per_page_from_links(soup, url)
        if per:
            return int(start) // per + 1
    return None


def _per_page_from_links(soup: BeautifulSoup, url: str) -> int | None:
    vals = set()
    for cont in pagination_containers(soup):
        for a in cont.find_all("a", href=True):
            u = absolutize(a["href"], url)
            if not u:
                continue
            for k, v in parse_qsl(urlsplit(u).query):
                if k in ("start", "offset") and v.isdigit():
                    vals.add(int(v))
    vals.add(0)
    s = sorted(vals)
    diffs = [b - a for a, b in zip(s, s[1:]) if b - a > 0]
    return min(diffs) if diffs else None


def infer_page_number(soup: BeautifulSoup, url: str, spec: PlatformSpec | None = None) -> int | None:
    n = _current_from_dom(soup, spec)
    if n is not None:
        return n
    n = _current_from_url(url, spec, soup)
    if n is not None:
        return n
    if not pagination_containers(soup):
        return 1
    return None


def _is_next_anchor(a: Tag) -> tuple[bool, str]:
    rel = a.get("rel") or []
    if isinstance(rel, str):
        rel = rel.split()
    if "next" in [r.lower() for r in rel]:
        return True, "rel=next"
    toks = [t.lower() for t in tokens(a)]
    label = " ".join(filter(None, [a.get("aria-label"), a.get("title")])).strip()
    text = text_of(a)
    if LAST_TEXT.search(text) or LAST_TEXT.search(label):
        return False, ""
    if any(re.search(r"(^|[-_])next([-_]|$)", t) for t in toks):
        return True, "class=next"
    if label and re.search(r"\bnext\b|weiter|suivant|siguiente|próxim|avanti|следующ|下一|次", label, re.I):
        return True, "label=next"
    if text and (NEXT_TEXT.match(text) or _ARROWS_ONLY.match(text)) and not PREV_TEXT.search(text):
        return True, "text=next"
    # arrow icons inside the anchor
    if not text:
        for child in a.find_all(True):
            if any(re.search(r"(^|[-_])(next|chevron-right|arrow-right|angle-right)([-_]|$)", t.lower())
                   for t in tokens(child)):
                return True, "icon=next"
    return False, ""


def _has_prev_control(soup: BeautifulSoup, url: str) -> bool:
    for a in soup.find_all("a", href=True):
        rel = a.get("rel") or []
        rel = rel if isinstance(rel, list) else rel.split()
        label = " ".join(filter(None, [a.get("aria-label"), a.get("title"), text_of(a)]))
        toks = [t.lower() for t in tokens(a)]
        if "prev" in [r.lower() for r in rel] or any(re.search(r"(^|[-_])(prev|previous)([-_]|$)", t) for t in toks) \
                or (label and re.match(r"^\s*([‹«←<]+\s*)?(prev|previous|zurück|précédente?|anterior|precedente)", label, re.I)):
            if absolutize(a["href"], url):
                return True
    return False


def discover_pagination(
    soup: BeautifulSoup,
    url: str,
    start_url: str,
    spec: PlatformSpec | None = None,
    profile: SiteProfile | None = None,
    visited: set[str] | None = None,
    allowed_domains: list[str] | None = None,
    thread_urls: set[str] | None = None,
    has_threads: bool = True,
) -> PaginationResult:
    visited = visited or set()
    thread_urls = thread_urls or set()
    allowed = allowed_domains or []
    cur_norm = normalize_url(url)
    cur_base = listing_base(url)
    current = infer_page_number(soup, url, spec)
    res = PaginationResult(current_page=current)

    def acceptable(u: str | None, strict_base: bool) -> bool:
        if not u or not in_scope(u, start_url, allowed):
            return False
        nu = normalize_url(u)
        if nu == cur_norm or nu in visited or nu in thread_urls:
            return False
        if strict_base and listing_base(u) != cur_base:
            return False
        return True

    cands: list[PageCandidate] = []

    def add(u: str | None, text: str, reason: str, score: float, strict: bool) -> None:
        if acceptable(u, strict):
            cands.append(PageCandidate(normalize_url(u), text[:60], reason, score))

    # 1. profile selector
    if profile and profile.next_page_selector:
        for el in safe_select(soup, profile.next_page_selector):
            href = el.get("href")
            add(absolutize(href or "", url), text_of(el), "profile selector", 0.99, False)
    # 2. platform selectors
    if spec:
        for sel in spec.next_page:
            for el in safe_select(soup, sel):
                ok, why = _is_next_anchor(el) if el.name == "a" else (True, "link")
                if el.name == "link" or ok or sel.startswith("a.pageNav-jump--next"):
                    add(absolutize(el.get("href", ""), url), text_of(el), f"platform selector {sel}", 0.95, True)
    # 3. <link rel=next>
    for link in soup.find_all("link", href=True):
        rel = link.get("rel") or []
        if "next" in [r.lower() for r in (rel if isinstance(rel, list) else rel.split())]:
            add(absolutize(link["href"], url), "", "<link rel=next>", 0.9, True)
    # 4. anchors that look like a "next" control
    for a in soup.find_all("a", href=True):
        ok, why = _is_next_anchor(a)
        if ok:
            in_pag = any(PAGINATION_HINT.search(t) for anc in ancestors(a) for t in tokens(anc))
            score = {"rel=next": 0.9, "class=next": 0.85, "label=next": 0.85, "text=next": 0.8,
                     "icon=next": 0.75}[why] + (0.05 if in_pag else 0)
            add(absolutize(a["href"], url), text_of(a), f"next control ({why})", score, True)
    # 5. numbered pagination
    numbered: dict[int, str] = {}
    for cont in pagination_containers(soup):
        for a in cont.find_all("a", href=True):
            n = _int(text_of(a))
            u = absolutize(a["href"], url)
            if n is not None and u:
                numbered.setdefault(n, u)
    if numbered:
        res.last_page = max(numbered)
        if current is not None:
            res.last_page = max(res.last_page, current)
            if current + 1 in numbered:
                add(numbered[current + 1], str(current + 1), "numbered pagination (current+1)", 0.75, True)

    cands.sort(key=lambda c: -c.score)
    # de-duplicate by URL keeping the best reason
    seen: set[str] = set()
    uniq = []
    for c in cands:
        if c.url not in seen:
            seen.add(c.url)
            uniq.append(c)
    res.candidates = uniq
    if uniq:
        best = uniq[0]
        res.next_url, res.method, res.confidence = best.url, best.reason, best.score
        # agreement between independent signals raises confidence
        agreeing = {c.reason for c in cands if c.url == best.url}
        res.confidence = min(0.99, best.score + 0.03 * (len(agreeing) - 1))
        return res

    # Nothing found: decide between "last page", "inferred" and "ask the user".
    if numbered and current is not None and current >= max(numbered):
        res.is_last = True
        res.method = "last page (numbered pagination)"
        res.confidence = 0.9
        return res
    if current is not None and current > 1 and _has_prev_control(soup, url):
        res.is_last = True
        res.method = "last page (previous-page link but no next-page link)"
        res.confidence = 0.8
        return res
    if spec and spec.page_param and has_threads:
        key, first = spec.page_param
        idx = (current or 1) - 1 + first
        nxt = set_query_param(url, key, idx + 1)
        if acceptable(nxt, False):
            res.next_url, res.method, res.confidence, res.inferred = (
                normalize_url(nxt), f"inferred ?{key}= parameter ({spec.label})", 0.6, True)
            return res
    # page-like links the user could pick from
    pagey = []
    for a in soup.find_all("a", href=True):
        u = absolutize(a["href"], url)
        if not u or not in_scope(u, start_url, allowed):
            continue
        t = text_of(a)
        if _int(t) is not None or re.search(r"(page|[?&](p|pg|start|offset)=)", u, re.I):
            if normalize_url(u) != cur_norm:
                pagey.append(PageCandidate(normalize_url(u), t[:60], "page-like link", 0.3))
    if current is not None:
        for rx in _URL_PAGE_PATTERNS[:2]:
            if rx.search(url):
                guess = rx.sub(lambda m: m.group(0).replace(m.group(1), str(current + 1)), url, count=1)
                pagey.insert(0, PageCandidate(normalize_url(guess), "", "URL pattern increment (unverified)", 0.4))
                break
    res.candidates = pagey[:40]
    if current is not None:
        # page-like links that all point backwards mean this is the last page
        nums = [_current_from_url(c.url, spec, None) for c in pagey if c.reason == "page-like link"]
        if pagey and nums and all(n is not None and n <= current for n in nums):
            res.is_last = True
            res.method = "last page (all page links point backwards)"
            res.confidence = 0.75
            res.candidates = pagey[:40]
            return res
    if pagey:
        res.needs_confirmation = True
        res.method = "unknown - confirmation required"
    else:
        res.is_last = True
        res.method = "no pagination found (single page)"
        res.confidence = 0.6
    return res
