"""Reddit collection through Reddit's public JSON listings.

A subreddit is one endlessly scrolling list, so it is read as a feed: every request returns up to 100 posts
(title + the owner's text) and a cursor ("after") for the next 100. No post pages need to be opened, and no
comments are read. Only text ("self") posts are collected; image/link posts and ads are skipped.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit

from .platforms import EMPTY_BODY_TEXT
from .textutil import html_to_text, soup_from_html
from .urls import is_reddit

USER_AGENT = "forum-post-extractor/1.0 (personal text-collection tool; run locally by its user)"
SORTS = ("new", "hot", "top", "rising", "controversial", "best")


@dataclass
class RedditTarget:
    base: str  # https://www.reddit.com (or a test server)
    subreddit: str
    sort: str = "new"
    t: str | None = None  # time range for top/controversial
    post_id: str | None = None  # set when the URL points to one post


@dataclass
class RedditPost:
    id: str
    title: str
    url: str
    text: str
    is_self: bool
    promoted: bool
    stickied: bool
    author: str = ""


def parse_target(url: str) -> RedditTarget | None:
    parts = urlsplit(url.strip())
    m = re.search(r"/r/([A-Za-z0-9_]+)(?:/(comments)/([a-z0-9]+))?(?:/([a-z]+))?", parts.path)
    if not m:
        return None
    if is_reddit(url):
        base = "https://www.reddit.com"
    else:
        base = f"{parts.scheme}://{parts.netloc}{parts.path[: m.start()]}"
    q = dict(parse_qsl(parts.query))
    sort = (m.group(4) or "new").lower() if not m.group(2) else "new"
    if sort not in SORTS:
        sort = "new"
    return RedditTarget(base=base, subreddit=m.group(1), sort=sort, t=q.get("t"),
                        post_id=m.group(3) if m.group(2) else None)


def listing_url(t: RedditTarget, after: str | None = None) -> str:
    params = [("limit", "100"), ("raw_json", "1")]
    if t.t and t.sort in ("top", "controversial"):
        params.append(("t", t.t))
    if after:
        params.append(("after", after))
    return f"{t.base}/r/{t.subreddit}/{t.sort}.json?{urlencode(params)}"


def post_url(t: RedditTarget) -> str:
    return f"{t.base}/r/{t.subreddit}/comments/{t.post_id}.json?raw_json=1&limit=1"


def _post_text(d: dict) -> str:
    html = d.get("selftext_html") or ""
    if html:
        soup = soup_from_html(html)
        root = soup.select_one("div.md") or soup.body or soup
        text = html_to_text(root)
    else:
        text = (d.get("selftext") or "").strip()
    if text in ("[removed]", "[deleted]"):
        return ""
    return text


def _post(d: dict, base: str) -> RedditPost:
    permalink = d.get("permalink") or ""
    url = (base + permalink) if permalink.startswith("/") else (d.get("url") or "")
    return RedditPost(
        id=str(d.get("id") or d.get("name") or ""),
        title=(d.get("title") or "").strip(),
        url=url,
        text=_post_text(d),
        is_self=bool(d.get("is_self")),
        promoted=bool(d.get("promoted")) or bool(d.get("is_created_from_ads_ui")),
        stickied=bool(d.get("stickied")),
        author=d.get("author") or "",
    )


def parse_listing(data, base: str) -> tuple[list[RedditPost], str | None]:
    """Posts and the cursor for the next request from a listing (or a post page) JSON response."""
    if isinstance(data, list):  # /comments/<id>.json -> [post listing, comments listing]
        data = data[0] if data else {}
    payload = (data or {}).get("data") or {}
    posts = [_post(c.get("data") or {}, base) for c in payload.get("children") or [] if c.get("kind") == "t3"]
    return posts, payload.get("after")


def wanted(p: RedditPost) -> bool:
    """Text posts only (the owner's question); image/link posts and ads are skipped."""
    return p.is_self and not p.promoted and bool(p.title)


def record_text(p: RedditPost) -> str:
    return p.text or EMPTY_BODY_TEXT
