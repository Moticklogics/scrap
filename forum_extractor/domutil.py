"""Small DOM helpers shared by the detection, listing and extraction engines."""
from __future__ import annotations

import re
from typing import Iterable

from bs4 import BeautifulSoup, Tag

CHROME_TAGS = {"nav", "footer", "aside"}
_CHROME_HINT = re.compile(
    r"(^|[-_])(nav|navbar|navigation|menu|breadcrumbs?|footer|sidebar|widget|cookie|banner|share|"
    r"advert|ads?|sponsor|offcanvas)([-_]|$)",
    re.I,
)
_STATE_CLASS = re.compile(
    r"^(active|selected|current|is-.*|js-.*|has-.*|odd|even|first|last|bg\d|row\d|alt\d?|sticky|unread|read|"
    r"new|hot|locked|hidden|visible|open|closed|clearfix|onscreen-post|highlighted|selected-.*|lazy.*)$",
    re.I,
)
_HASHY_CLASS = re.compile(r"(^(css|sc|jsx|emotion|svelte)-)|\d{3,}|^[a-z]{1,3}\d", re.I)
_SAFE_CLASS = re.compile(r"^-?[A-Za-z_][A-Za-z0-9_-]*$")
_SAFE_ID = re.compile(r"^[A-Za-z][A-Za-z_-]*$")

META_TOKEN = re.compile(
    r"^(.*[-_])?(user|users|username|author|avatar|date|time|timestamp|meta|signature|sig|byline|footer|header|"
    r"actions|controls|reactions|likes|share|toolbar|profile|rank|badge|stats|poster|postinfo|userinfo|vote|votes|"
    r"permalink|attribution|posted|joined|report|menu|buttons?|tools)([-_].*)?$",
    re.I,
)
POST_HINT = re.compile(
    r"(^|[-_])(post|message|msg|comment|reply|entry|postbit|postcontainer)([-_]|$)",
    re.I,
)
QUOTE_HINT = re.compile(
    r"(^|[-_])(quote|quotes|bbcodequote|quotecontainer|blockquote|userquote|cite|citation)([-_]|$)", re.I)
_META_SUBSTR = re.compile(
    r"((^|[-_]|post|msg|message|comment|reply)(date|datetime|timestamp)([-_]|$)|author|avatar|signature|byline|"
    r"username|userinfo|postprofile|poster)",
    re.I,
)
_QUOTE_INTRO = re.compile(r"(wrote|said|quote|schrieb|a écrit|escribió|написал|originally posted)", re.I)


def classes(el: Tag) -> list[str]:
    c = el.get("class") or []
    return [x for x in c if isinstance(x, str)]


def stable_classes(el: Tag) -> list[str]:
    return [c for c in classes(el) if not _STATE_CLASS.match(c) and not _HASHY_CLASS.search(c)
            and _SAFE_CLASS.match(c)]


def signature(el: Tag) -> str:
    return el.name + "".join("." + c for c in sorted(stable_classes(el)))


def css_simple(el: Tag) -> str:
    """tag.class1.class2 selector for one element (stable classes only)."""
    return el.name + "".join("." + c for c in stable_classes(el))


def norm_space(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def text_of(el: Tag) -> str:
    return norm_space(el.get_text(" ", strip=True))


def link_text_len(el: Tag) -> int:
    return sum(len(text_of(a)) for a in el.find_all("a"))


def link_density(el: Tag) -> float:
    t = len(text_of(el))
    if t == 0:
        return 1.0
    return min(1.0, link_text_len(el) / t)


def tokens(el: Tag) -> list[str]:
    """Class names, id and role-like attribute values (component, itemprop, data-role)."""
    out = classes(el)
    for attr in ("id", "component", "data-component", "itemprop", "data-role"):
        v = el.get(attr)
        if isinstance(v, str) and v:
            out = out + [v.replace("/", "-")]
    return out


def similar_groups(elements: list[Tag], threshold: float = 0.5) -> list[list[Tag]]:
    """Group elements with the same tag whose stable class sets overlap (Jaccard >= threshold).

    Tolerates per-item modifier classes such as 'topic-owner', 'sticky' or 'first'."""
    groups: list[tuple[str, set[str], list[Tag]]] = []
    for el in elements:
        cs = set(stable_classes(el))
        for tag, rep, members in groups:
            if tag != el.name:
                continue
            union = rep | cs
            jac = 1.0 if not union else len(rep & cs) / len(union)
            if jac >= threshold:
                members.append(el)
                break
        else:
            groups.append((el.name, cs, [el]))
    return [m for _, _, m in groups]


_UI_LABEL = re.compile(r"^\s*(code|quote|spoiler|html|php|select all|expand|collapse|click to expand\.*)"
                       r"(\s*[:\-]?\s*(select all|expand|collapse|copy))?\s*:?\s*$", re.I)


def is_meta(el: Tag) -> bool:
    if el.name in ("time", "header", "footer"):
        return True
    if any(META_TOKEN.match(t) or _META_SUBSTR.search(t) for t in tokens(el)):
        return True
    if el.name in ("div", "p", "span", "dt", "figcaption", "strong", "b") and len(el.get_text()) < 40 \
            and _UI_LABEL.match(el.get_text(" ", strip=True) or "x"):
        return True
    return False


_CONTENTISH = re.compile(r"(content|message|post|text|body)", re.I)


def is_quote(el: Tag) -> bool:
    """Quoted material. Some platforms (vBulletin, XenForo 1) use <blockquote> for the post body itself."""
    toks = tokens(el)
    if any(QUOTE_HINT.search(t) for t in toks):
        return True
    if el.name == "blockquote":
        return not any(_CONTENTISH.search(t) for t in toks)
    # wrappers such as <div><i>Quote from X:</i><blockquote>..</blockquote></div> or <figure>
    if el.name in ("div", "figure", "aside", "section"):
        bq = el.find("blockquote", recursive=False)
        if bq is not None and is_quote(bq):
            rest = len(norm_space(el.get_text(" "))) - len(norm_space(bq.get_text(" ")))
            if rest < 80 and (el.name == "figure" or _QUOTE_INTRO.search(el.get_text(" ")[:200])):
                return True
    return False


def is_meta_or_quote(el: Tag) -> bool:
    return is_meta(el) or is_quote(el)


def ancestors(el: Tag) -> Iterable[Tag]:
    p = el.parent
    while p is not None and isinstance(p, Tag) and p.name not in ("[document]",):
        yield p
        p = p.parent


def in_chrome(el: Tag) -> bool:
    """True if the element lives in page navigation, footers, sidebars or similar furniture."""
    nested_in_content = False
    for a in [el, *ancestors(el)]:
        if a.name in ("body", "html"):
            break
        if a.name in ("article",):
            nested_in_content = True
        if a.name in CHROME_TAGS:
            return True
        if a.name == "header" and not nested_in_content:
            # page-level header (banner); headers inside articles/posts are content.
            return True
        if a.get("role") in ("navigation", "banner", "contentinfo", "complementary"):
            return True
        if any(_CHROME_HINT.search(t) for t in tokens(a)):
            return True
    return False


def in_quote(el: Tag, stop: Tag | None = None) -> bool:
    for a in [el, *ancestors(el)]:
        if stop is not None and a is stop:
            return False
        if is_quote(a):
            return True
    return False


def is_ancestor(anc: Tag, el: Tag) -> bool:
    return any(a is anc for a in ancestors(el))


def safe_select(root: Tag | BeautifulSoup, selector: str) -> list[Tag]:
    if not selector or not selector.strip():
        return []
    try:
        return list(root.select(selector))
    except Exception:
        return []


def safe_select_one(root: Tag | BeautifulSoup, selector: str) -> Tag | None:
    res = safe_select(root, selector)
    return res[0] if res else None


def _anc_selector(a: Tag) -> str | None:
    aid = a.get("id")
    if isinstance(aid, str) and _SAFE_ID.match(aid):
        return f"{a.name}#{aid}"
    sc = stable_classes(a)
    if sc:
        return a.name + "".join("." + c for c in sc)
    return None


def group_selector(soup: BeautifulSoup | Tag, elements: list[Tag]) -> str:
    """Build a CSS selector that matches (approximately) exactly the given group of elements."""
    if not elements:
        return ""
    first = elements[0]
    common = set(stable_classes(first))
    for e in elements[1:]:
        common &= set(stable_classes(e))
    ordered = [c for c in stable_classes(first) if c in common]
    base = first.name + "".join("." + c for c in ordered)
    target = {id(e) for e in elements}

    def score(sel: str) -> tuple[bool, int]:
        got = {id(e) for e in safe_select(soup, sel)}
        return target <= got, len(got - target)

    ok, extra = score(base)
    if ok and extra == 0:
        return base
    best, best_extra = (base, extra) if ok else ("", 10**9)
    # Qualify with ancestors of the first element (closest first).
    chain: list[str] = []
    for anc in list(ancestors(first))[:6]:
        s = _anc_selector(anc)
        if not s:
            continue
        chain.insert(0, s)
        for sel in (f"{s} {base}", " ".join(chain + [base])):
            ok, extra = score(sel)
            if ok and extra < best_extra:
                best, best_extra = sel, extra
                if extra == 0:
                    return best
    return best or base


def relative_selector(container: Tag, el: Tag) -> str:
    """Selector for `el` relative to `container` (first match must be `el`)."""
    if el is container:
        return ""
    candidates = []
    simple = css_simple(el)
    candidates.append(simple)
    if el.get("itemprop"):
        candidates.insert(0, f'{el.name}[itemprop="{el.get("itemprop")}"]')
    parent = el.parent
    if isinstance(parent, Tag) and parent is not container:
        candidates.append(f"{css_simple(parent)} > {simple}")
    for sel in candidates:
        if safe_select_one(container, sel) is el:
            return sel
    # positional fallback
    path = []
    cur = el
    while cur is not None and cur is not container:
        par = cur.parent
        if par is None:
            break
        same = [c for c in par.find_all(cur.name, recursive=False)]
        idx = next(i for i, c in enumerate(same) if c is cur) + 1
        path.insert(0, f"{cur.name}:nth-of-type({idx})")
        cur = par
    return ":scope > " + " > ".join(path) if path else ""


def main_text_len(soup: BeautifulSoup) -> int:
    body = soup.body or soup
    return len(text_of(body))
