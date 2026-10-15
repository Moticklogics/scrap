"""Configuration objects: job settings and per-forum site profiles."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any


def _filter_kwargs(cls, data: dict[str, Any]) -> dict[str, Any]:
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in (data or {}).items() if k in names}


@dataclass
class SiteProfile:
    """Selectors describing one forum. Generated automatically or edited by the user.

    Every selector is a CSS selector evaluated with soupsieve (BeautifulSoup).
    Empty strings mean "not configured - use automatic detection".
    """

    domain: str
    platform: str = "custom"
    listing_container_selector: str = ""  # region of a listing page holding the thread list
    thread_link_selector: str = ""  # anchors that point to discussion threads
    thread_title_selector: str = ""  # optional, relative to the thread anchor
    next_page_selector: str = ""  # anchor of the "next listing page" link
    post_container_selector: str = ""  # one element per post on a thread page
    first_post_selector: str = ""  # optional, selects the original post container directly
    post_body_selector: str = ""  # relative to the post container
    exclude_selectors: list[str] = field(default_factory=list)  # removed from the post body
    source: str = "auto"  # "auto" (generated) or "user" (edited/confirmed by the user)
    confidence: float = 0.0
    notes: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SiteProfile":
        kwargs = _filter_kwargs(cls, data)
        excl = kwargs.get("exclude_selectors")
        if isinstance(excl, str):
            kwargs["exclude_selectors"] = [s.strip() for s in excl.splitlines() if s.strip()]
        return cls(**kwargs)

    def has_listing_selectors(self) -> bool:
        return bool(self.thread_link_selector)

    def has_post_selectors(self) -> bool:
        return bool(self.post_container_selector or self.first_post_selector)


@dataclass
class JobConfig:
    start_url: str
    output_dir: str = "output"
    max_pages: int | None = None  # None = until the last page
    max_posts: int | None = None  # stop after this many saved posts (used for Reddit)
    source: str = "auto"  # "auto" (detect) or "reddit" (Reddit JSON feed)
    posts_per_file: int | None = None  # rotate output by number of posts instead of listing pages (Reddit)
    pages_per_file: int = 1100
    request_delay: float = 1.0  # seconds between request starts (global)
    max_concurrency: int = 2  # concurrent thread pages
    skip_duplicates: bool = True
    confidence_threshold: float = 0.6
    mode: str = "auto"  # "auto" or "manual" (manual = only the saved site profile)
    file_prefix: str = "Forum_Pages"
    respect_robots: bool = True
    page_timeout: float = 30.0  # seconds per navigation
    max_retries: int = 3  # per request
    exclude_quotes: bool = True  # strip quoted text from inside the original post
    allowed_domains: list[str] = field(default_factory=list)  # extra hosts considered in scope
    on_unknown_pagination: str = "ask"  # "ask" (UI confirmation) or "stop"
    ai_classifier: str = "none"  # "none" or "ollama" (experimental, local only)
    ai_model: str = "llama3.1"
    ai_endpoint: str = "http://127.0.0.1:11434"
    headless: bool = True
    wait_for_network_idle: bool = True
    user_agent: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JobConfig":
        kw = _filter_kwargs(cls, data)
        for key in ("max_pages", "max_posts", "posts_per_file"):
            if kw.get(key) in ("", 0, "0"):
                kw[key] = None
            elif kw.get(key) is not None:
                kw[key] = int(kw[key])
        for key, typ in (("pages_per_file", int), ("max_concurrency", int), ("max_retries", int),
                         ("request_delay", float), ("confidence_threshold", float), ("page_timeout", float)):
            if key in kw and kw[key] is not None:
                kw[key] = typ(kw[key])
        cfg = cls(**kw)
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if not self.start_url.lower().startswith(("http://", "https://")):
            raise ValueError("Starting URL must begin with http:// or https://")
        if self.pages_per_file < 1:
            raise ValueError("Pages per file must be at least 1")
        if self.max_concurrency < 1:
            raise ValueError("Maximum concurrent threads must be at least 1")
        if not 0.0 <= self.confidence_threshold <= 1.0:
            raise ValueError("Confidence threshold must be between 0 and 1")
        if self.mode not in ("auto", "manual"):
            raise ValueError("Mode must be 'auto' or 'manual'")
        if self.request_delay < 0:
            raise ValueError("Request delay cannot be negative")
