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


# --------------------------------------------------------------------------- official API (OAuth)

def credentials_path(data_dir) -> "Path":
    from pathlib import Path
    return Path(data_dir) / "reddit_api.json"


def load_credentials(data_dir) -> dict | None:
    import json
    p = credentials_path(data_dir)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if d.get("client_id") and d.get("client_secret"):
        return d
    return None


def save_credentials(data_dir, client_id: str, client_secret: str, username: str = "") -> None:
    import json
    p = credentials_path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"client_id": client_id.strip(), "client_secret": client_secret.strip(),
                             "username": re.sub(r"^/?u/", "", username.strip())}), encoding="utf-8")


def delete_credentials(data_dir) -> None:
    try:
        credentials_path(data_dir).unlink()
    except OSError:
        pass


class RedditAuthError(Exception):
    pass


class RedditClient:
    """Reads Reddit listings: through the official API when an app key is saved, otherwise the public feed."""

    def __init__(self, browser, target: RedditTarget, creds: dict | None = None, user_agent: str = ""):
        self.browser = browser
        self.t = target
        self.creds = creds
        self._token: str | None = None
        self._token_expiry = 0.0
        name = (creds or {}).get("username") or "unknown"
        self.ua = user_agent or (f"windows:forum-post-extractor:1.0 (by /u/{name})" if creds else USER_AGENT)
        if target.base == "https://www.reddit.com":
            self.token_url = "https://www.reddit.com/api/v1/access_token"
            self.api_base = "https://oauth.reddit.com"
        else:  # test server
            self.token_url = target.base + "/api/v1/access_token"
            self.api_base = target.base + "/oauth"

    @property
    def api_mode(self) -> bool:
        return self.creds is not None

    async def _auth_headers(self) -> dict:
        import base64
        import json
        import time
        if not self.api_mode:
            return {}
        if not self._token or time.time() > self._token_expiry - 60:
            basic = base64.b64encode(f"{self.creds['client_id']}:{self.creds['client_secret']}".encode()).decode()
            status, body = await self.browser.post_form(
                self.token_url, {"grant_type": "client_credentials"},
                {"Authorization": f"Basic {basic}", "User-Agent": self.ua})
            try:
                data = json.loads(body)
            except ValueError:
                data = {}
            if status != 200 or "access_token" not in data:
                raise RedditAuthError(
                    f"Reddit did not accept the API key (HTTP {status}{', ' + str(data.get('error')) if data.get('error') else ''}). "
                    "Check the client ID and secret in the Reddit tab (the app type must be 'script').")
            self._token = data["access_token"]
            self._token_expiry = time.time() + float(data.get("expires_in", 3600))
        return {"Authorization": f"bearer {self._token}"}

    def listing_url(self, after: str | None = None) -> str:
        if not self.api_mode:
            return listing_url(self.t, after)
        url = listing_url(self.t, after).replace(f"{self.t.base}/r/", f"{self.api_base}/r/", 1)
        return url.replace(f"/{self.t.sort}.json?", f"/{self.t.sort}?", 1)

    def post_url(self) -> str:
        if not self.api_mode:
            return post_url(self.t)
        return f"{self.api_base}/r/{self.t.subreddit}/comments/{self.t.post_id}?raw_json=1&limit=1"

    async def get(self, url: str):
        return await self.browser.fetch_json(url, self.ua, await self._auth_headers())

    async def check(self) -> None:
        """Log in with the saved key (raises RedditAuthError when it is wrong)."""
        await self._auth_headers()
