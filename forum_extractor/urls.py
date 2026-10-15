"""URL normalisation, thread identity and scope rules."""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

_TRACKING_PARAMS = {
    "sid", "phpsessid", "fbclid", "gclid", "ref", "hilit", "highlight", "utm_source", "utm_medium",
    "utm_campaign", "utm_term", "utm_content",
}
# Query parameters that only select a sub-page / post inside a thread.
_THREAD_PAGE_PARAMS = {"start", "page", "pg", "view", "goto", "postcount", "p", "post", "unread", "u"}

_HEX32 = re.compile(r"^[0-9a-f]{32}$", re.I)

# Path suffixes that select a page or post inside a thread rather than the thread itself.
_THREAD_SUFFIXES = [
    re.compile(r"/(?:unread|latest|newest|last|first|post-\d+|page-\d+)/?$", re.I),  # XenForo
    re.compile(r"(/t/[^/]+/\d+)/\d+/?$", re.I),  # Discourse /t/slug/id/postnum
    re.compile(r"(/topic/\d+(?:/[^/]+)?)/\d+/?$", re.I),  # NodeBB /topic/id/slug/index
    re.compile(r"(/d/\d+(?:-[^/]*)?)/\d+/?$", re.I),  # Flarum /d/id-slug/near
    re.compile(r"(/discussion/\d+(?:/[^/]+)?)/p\d+/?$", re.I),  # Vanilla /discussion/id/slug/p2
    re.compile(r"/page/\d+/?$", re.I),
]


_REDDIT_HOSTS = {"reddit.com", "www.reddit.com", "new.reddit.com", "np.reddit.com", "m.reddit.com",
                 "sh.reddit.com", "i.reddit.com"}


def to_old_reddit(url: str) -> str:
    """Reddit's server-rendered interface (old.reddit.com) has real pagination; the new one scrolls forever."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host in _REDDIT_HOSTS:
        return urlunsplit((parts.scheme or "https", "old.reddit.com", parts.path, parts.query, parts.fragment))
    return url


def is_reddit(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host == "reddit.com" or host.endswith(".reddit.com")


def absolutize(href: str, base_url: str) -> str | None:
    href = (href or "").strip()
    if not href or href.startswith(("javascript:", "mailto:", "tel:", "data:")):
        return None
    if href.startswith("#"):
        return None
    url = urljoin(base_url, href)
    if not url.lower().startswith(("http://", "https://")):
        return None
    return url


def host_of(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host


def domain_key(url: str) -> str:
    host = host_of(url)
    return host[4:] if host.startswith("www.") else host


def _clean_query(query: str, drop: set[str]) -> str:
    pairs = parse_qsl(query, keep_blank_values=True)
    out = []
    for k, v in pairs:
        kl = k.lower()
        if kl in drop or kl.startswith("utm_"):
            continue
        if kl == "s" and (_HEX32.match(v) or v == ""):
            continue  # vBulletin session hash
        out.append((k, v))
    return urlencode(out, doseq=True, safe="/:,")


def _split_query(query: str) -> tuple[str, str]:
    """XenForo style URLs put the route in the query (index.php?threads/x.1/&foo=1)."""
    if not query:
        return "", ""
    first, _, rest = query.partition("&")
    if "=" not in first:
        return first, rest
    return "", query


def normalize_url(url: str) -> str:
    """Normalise a URL for comparison: lower-case host, no fragment, no tracking parameters."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    if (scheme == "http" and netloc.endswith(":80")) or (scheme == "https" and netloc.endswith(":443")):
        netloc = netloc.rsplit(":", 1)[0]
    route, rest = _split_query(parts.query)
    rest = _clean_query(rest, _TRACKING_PARAMS)
    query = "&".join(x for x in (route, rest) if x)
    path = parts.path or "/"
    return urlunsplit((scheme, netloc, path, query, ""))


def canonical_thread_url(url: str) -> str:
    """URL of page 1 of a thread, with post anchors / page selectors removed."""
    parts = urlsplit(normalize_url(url))
    route, rest = _split_query(parts.query)
    drop = set(_TRACKING_PARAMS) | _THREAD_PAGE_PARAMS
    pairs = parse_qsl(rest, keep_blank_values=True)
    keys = {k.lower() for k, _ in pairs}
    if "t" not in keys and "topic" not in keys and "threadid" not in keys and "id" not in keys:
        # 'p' is a post id in phpBB/vBulletin only when a topic id is present; otherwise keep it.
        drop.discard("p")
    rest = _clean_query(rest, drop)
    path = parts.path
    if route:
        for rx in _THREAD_SUFFIXES:
            route = _strip_suffix(rx, route)
        if not route.endswith("/") and re.search(r"\.\d+$", route):
            route += "/"
    else:
        for rx in _THREAD_SUFFIXES:
            path = _strip_suffix(rx, path)
    query = "&".join(x for x in (route, rest) if x)
    return urlunsplit((parts.scheme, parts.netloc, path, query, ""))


def _strip_suffix(rx: re.Pattern, s: str) -> str:
    m = rx.search(s)
    if not m:
        return s
    if m.groups():
        return s[: m.start()] + m.group(1)
    stripped = s[: m.start()]
    return stripped + "/" if s.endswith("/") else stripped


_XF_THREAD = re.compile(r"threads/(?:[^/]*\.)?(\d+)/?", re.I)


def thread_key(url: str) -> str:
    """Stable identity for a thread used for duplicate detection."""
    canon = canonical_thread_url(url)
    parts = urlsplit(canon)
    host = parts.netloc[4:] if parts.netloc.startswith("www.") else parts.netloc
    m = _XF_THREAD.search(parts.path + "?" + parts.query)
    if m:
        return f"{host}|xf-thread|{m.group(1)}"
    for rx, name in ((r"/comments/([a-z0-9]+)", "reddit"), (r"/t/[^/]+/(\d+)", "discourse"), (r"/topic/(\d+)", "nodebb"),
                     (r"/d/(\d+)", "flarum"), (r"/discussion/(\d+)", "vanilla")):
        m = re.search(rx, parts.path)
        if m:
            return f"{host}|{name}|{m.group(1)}"
    path = parts.path.rstrip("/") or "/"
    return f"{host}|{path}?{parts.query}"


def in_scope(url: str, start_url: str, allowed_domains: list[str] | None = None) -> bool:
    host = host_of(url)
    base = domain_key(start_url)
    if not host:
        return False
    hosts = {base} | {d.lower().lstrip(".") for d in (allowed_domains or []) if d}
    h = host[4:] if host.startswith("www.") else host
    return any(h == d or h.endswith("." + d) for d in hosts)


_SLUG = re.compile(r"[a-z0-9%]+(?:[-_][a-z0-9%]+)+", re.I)


def url_template(url: str) -> str:
    """Shape of a URL with variable parts replaced, used to group similar links."""
    parts = urlsplit(url)
    route, rest = _split_query(parts.query)
    segs = []
    for seg in (parts.path + ("?" + route if route else "")).split("/"):
        seg = _SLUG.sub("{s}", seg)
        seg = re.sub(r"\d+", "{n}", seg)
        segs.append(seg)
    keys = sorted({k.lower() for k, _ in parse_qsl(rest, keep_blank_values=True)} - _TRACKING_PARAMS - {"s"})
    tmpl = "/".join(segs)
    if keys:
        tmpl += "?" + "&".join(keys)
    return tmpl


_PAGE_PATTERNS = [
    re.compile(r"/page[-/]?\d+/?$", re.I),
    re.compile(r"/p\d+/?$", re.I),
]
_PAGE_PARAMS = {"page", "pg", "p", "start", "offset", "pagenumber", "pagenum", "count", "after", "before", "cursor"}


def listing_base(url: str) -> str:
    """A listing URL with its pagination component removed."""
    parts = urlsplit(normalize_url(url))
    route, rest = _split_query(parts.query)
    path = parts.path
    if route:
        for rx in _PAGE_PATTERNS:
            route = rx.sub("/", route)
    else:
        for rx in _PAGE_PATTERNS:
            path = rx.sub("/", path)
    rest = _clean_query(rest, _TRACKING_PARAMS | _PAGE_PARAMS)
    path = path.rstrip("/") or "/"
    route = route.rstrip("/")
    return urlunsplit((parts.scheme, parts.netloc, path, "&".join(x for x in (route, rest) if x), ""))


def set_query_param(url: str, key: str, value: int | str) -> str:
    parts = urlsplit(url)
    pairs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != key]
    pairs.append((key, str(value)))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(pairs), ""))


def get_query_param(url: str, key: str) -> str | None:
    for k, v in parse_qsl(urlsplit(url).query, keep_blank_values=True):
        if k == key:
            return v
    return None
