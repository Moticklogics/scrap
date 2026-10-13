"""Known forum software: fingerprints and extraction selectors.

Each selector list is ordered by preference; the first one that matches is used.
Adding a platform only requires adding a PlatformSpec here (no other code changes).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Signal:
    kind: str  # "css" | "generator" | "html" (regex on raw html) | "url" (regex on URL)
    value: str
    weight: float


@dataclass
class PlatformSpec:
    name: str
    label: str
    signals: list[Signal]
    listing_markers: list[str] = field(default_factory=list)
    thread_markers: list[str] = field(default_factory=list)
    thread_links: list[str] = field(default_factory=list)
    thread_title: str = ""  # relative to the anchor, when the anchor wraps more than the title
    next_page: list[str] = field(default_factory=list)
    current_page: list[str] = field(default_factory=list)
    post_containers: list[str] = field(default_factory=list)
    first_post: list[str] = field(default_factory=list)
    post_body: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    quote_exclude: list[str] = field(default_factory=list)
    # (query parameter, index of the first page) for infinite-scroll listings that accept ?page=N
    page_param: tuple[str, int] | None = None


PLATFORMS: list[PlatformSpec] = [
    PlatformSpec(
        name="xenforo2",
        label="XenForo 2",
        signals=[
            Signal("css", "html#XF", 4), Signal("css", "[data-xf-init]", 2), Signal("css", ".p-body-pageContent", 1),
            Signal("css", ".structItem--thread", 2), Signal("css", "article.message--post", 2),
            Signal("html", r"XenForo", 1), Signal("url", r"(index\.php\?|/)(forums|threads)/[^/]*\.\d+", 1),
        ],
        listing_markers=[".structItem--thread"],
        thread_markers=["article.message--post"],
        thread_links=[
            ".structItem--thread .structItem-title a[data-tp-primary]",
            ".structItem--thread .structItem-title a[href*='threads/']:not(.labelLink)",
        ],
        next_page=["a.pageNav-jump--next"],
        current_page=[".pageNav-page--current"],
        post_containers=["article.message--post"],
        post_body=[".message-body .bbWrapper", ".bbWrapper", ".message-userContent"],
        exclude=[".message-signature", ".js-selectToQuoteEnd", ".bbCodeBlock-expandLink",
                 ".bbCodeBlock-shrinkLink", ".message-lastEdit",
                 ".bbCodeBlock--code > .bbCodeBlock-title"],
        quote_exclude=[".bbCodeBlock--quote", "blockquote.bbCodeBlock"],
    ),
    PlatformSpec(
        name="xenforo1",
        label="XenForo 1",
        signals=[
            Signal("css", "html#XenForo", 4), Signal("css", ".discussionListItem", 2), Signal("css", "#messageList", 2),
            Signal("css", ".PageNav", 1), Signal("html", r"XenForo", 1),
            Signal("url", r"(index\.php\?|/)(forums|threads)/[^/]*\.\d+", 1),
        ],
        listing_markers=[".discussionListItem"],
        thread_markers=["#messageList li.message"],
        thread_links=[".discussionListItem h3.title a.PreviewTooltip", ".discussionListItem h3.title a[href*='threads/']"],
        next_page=[".PageNav a.text[href]"],
        current_page=[".PageNav a.currentPage"],
        post_containers=["#messageList > li.message", "li.message[id^='post-']"],
        post_body=["blockquote.messageText", ".messageText"],
        exclude=[".messageTextEndMarker", ".signature", ".baseHtml.signature", ".messageMeta", ".editDate"],
        quote_exclude=[".bbCodeQuote", "blockquote.quoteContainer"],
    ),
    PlatformSpec(
        name="phpbb",
        label="phpBB",
        signals=[
            Signal("css", "body#phpbb", 4), Signal("css", "a.topictitle", 2), Signal("css", "ul.topiclist", 2),
            Signal("css", "div.post div.postbody", 2), Signal("html", r"phpBB", 1),
            Signal("url", r"(viewforum|viewtopic)\.php", 2),
        ],
        listing_markers=["a.topictitle"],
        thread_markers=["div.post div.postbody"],
        thread_links=["ul.topiclist.topics a.topictitle", "a.topictitle"],
        next_page=[".pagination li.next a", ".pagination a[rel='next']"],
        current_page=[".pagination li.active span", ".pagination li.active", ".pagination strong"],
        post_containers=["div.post.has-profile", "div.post[id^='p']", "div#page-body div.post"],
        post_body=["div.postbody div.content", "div.content"],
        exclude=[".signature", ".notice", "dl.attachbox .attach-notice", ".postprofile", ".codebox > p"],
        quote_exclude=["blockquote"],
    ),
    PlatformSpec(
        name="vbulletin",
        label="vBulletin",
        signals=[
            Signal("generator", r"vBulletin", 4), Signal("css", "html#vbulletin_html", 4),
            Signal("css", "li.threadbit", 2), Signal("css", "a[id^='thread_title_']", 2),
            Signal("css", "li.postcontainer", 2), Signal("css", "div[id^='post_message_']", 2),
            Signal("css", ".b-post", 1), Signal("url", r"(showthread|forumdisplay)\.php", 2),
            Signal("html", r"vBulletin", 1),
        ],
        listing_markers=["li.threadbit", "a[id^='thread_title_']", ".topic-list-item"],
        thread_markers=["li.postcontainer", "div[id^='post_message_']", ".b-post"],
        thread_links=["li.threadbit h3.threadtitle a.title", "li.threadbit a.title",
                      "a[id^='thread_title_']", ".topic-list-item a.topic-title"],
        next_page=["a[rel='next']", ".pagination .prev_next a[rel='next']"],
        current_page=[".pagination span.selected a", ".pagination .selected", ".pagenav td.alt2 strong"],
        post_containers=["#posts li.postcontainer", "#posts > li.postbitlegacy", "#posts div[id^='edit']",
                         ".b-post.js-post"],
        post_body=["blockquote.postcontent", "div.postcontent", "div[id^='post_message_']",
                   ".js-post__content-text"],
        exclude=[".signaturecontainer", "blockquote.signature", ".signature", ".b-post__signature",
                 ".bbcode_description"],
        quote_exclude=[".bbcode_container:has(> .bbcode_quote)", ".bbcode_quote", "div.quote", ".bbcode-quote"],
    ),
    PlatformSpec(
        name="discourse",
        label="Discourse",
        signals=[
            Signal("generator", r"Discourse", 5), Signal("css", "#main-outlet", 1), Signal("css", ".topic-list", 1),
            Signal("css", "div.topic-body .cooked", 2), Signal("css", "#post_1", 1),
            Signal("css", "meta[name='discourse_theme_id'], meta[name='discourse_current_homepage']", 3),
            Signal("url", r"/t/[^/]+/\d+", 1),
        ],
        listing_markers=[".topic-list", ".topic-list-item"],
        thread_markers=["#post_1", ".topic-post", "div.crawler-post"],
        thread_links=["table.topic-list a.title.raw-topic-link", ".topic-list .main-link a.title",
                      "a.raw-topic-link", "#main-outlet a[itemprop='url'][href*='/t/']"],
        next_page=["a[rel='next']", "link[rel='next']"],
        post_containers=[".topic-post article[id^='post_']", "div.topic-body.crawler-post", "div.crawler-post"],
        first_post=["article#post_1", "#post_1", "article[data-post-number='1']"],
        post_body=[".cooked", "div.post[itemprop='text']", "[itemprop='text']", "[itemprop='articleBody']"],
        exclude=[".cooked-selection-barrier", ".post-links-container", "aside.onebox .source"],
        quote_exclude=["aside.quote"],
        page_param=("page", 0),
    ),
    PlatformSpec(
        name="nodebb",
        label="NodeBB",
        signals=[
            Signal("css", "[component='category/topic']", 3), Signal("css", "[component='post']", 3),
            Signal("css", "[component='post/content']", 2), Signal("html", r"NodeBB", 2),
            Signal("url", r"/topic/\d+", 1), Signal("url", r"/category/\d+", 1),
        ],
        listing_markers=["[component='category/topic']"],
        thread_markers=["[component='post']"],
        thread_links=["[component='category/topic'] [component='topic/header'] a",
                      "[component='category/topic'] h3 a[href*='/topic/']",
                      "[component='category/topic'] a[itemprop='url']"],
        next_page=["[component='pagination/next'] a", ".pagination .next a", "a[rel='next']", "link[rel='next']"],
        current_page=[".pagination li.active a", ".pagination li.active"],
        post_containers=["[component='post']"],
        first_post=["[component='post'][data-index='0']"],
        post_body=["[component='post/content']", ".content"],
        exclude=["[component='post/signature']", ".post-signature"],
        quote_exclude=["blockquote"],
        page_param=("page", 1),
    ),
    PlatformSpec(
        name="flarum",
        label="Flarum",
        signals=[
            Signal("css", "#flarum-loading", 4), Signal("css", "#flarum-content", 3), Signal("css", ".DiscussionList", 2),
            Signal("css", ".PostStream", 2), Signal("html", r"flarum", 2), Signal("url", r"/d/\d+", 1),
        ],
        listing_markers=[".DiscussionList", ".DiscussionListItem"],
        thread_markers=[".PostStream-item", "#flarum-content article .Post-body"],
        thread_links=[".DiscussionListItem a.DiscussionListItem-main", "#flarum-content li a[href*='/d/']"],
        thread_title=".DiscussionListItem-title",
        next_page=["a[rel='next']", "link[rel='next']", "#flarum-content a[href*='page=']"],
        post_containers=[".PostStream-item", "#flarum-content article"],
        first_post=[".PostStream-item[data-number='1']", ".PostStream-item[data-index='0']",
                    "#flarum-content article"],
        post_body=[".Post-body"],
        exclude=[".Post-footer", ".PostMention"],
        quote_exclude=["blockquote"],
        page_param=("page", 1),
    ),
    PlatformSpec(
        name="vanilla",
        label="Vanilla Forums",
        signals=[
            Signal("css", "body[id^='vanilla_']", 4), Signal("css", ".DataList.Discussions", 2),
            Signal("css", ".ItemDiscussion", 2), Signal("css", ".MessageList", 1), Signal("html", r"Vanilla", 1),
            Signal("url", r"/discussion/\d+", 1),
        ],
        listing_markers=[".DataList.Discussions", "table.DiscussionsTable"],
        thread_markers=[".MessageList .ItemDiscussion", "div.Discussion .Message"],
        thread_links=[".DataList.Discussions .ItemDiscussion .Title a", ".ItemDiscussion a.Title",
                      ".DiscussionsTable .DiscussionName a.Title"],
        next_page=["#PagerAfter a.Next", ".Pager a.Next", "a[rel='next']"],
        current_page=[".Pager a.Highlight", ".Pager .Highlight"],
        post_containers=[".MessageList .Item.ItemDiscussion, .MessageList .Item.ItemComment",
                         ".ItemDiscussion, .ItemComment"],
        first_post=[".MessageList .ItemDiscussion", "div.ItemDiscussion", "div.Discussion"],
        post_body=[".Message.userContent", ".Message"],
        exclude=[".UserSignature", ".Signature", ".Reactions", ".Meta"],
        quote_exclude=["blockquote.Quote", "blockquote.UserQuote", ".UserQuote", "blockquote"],
    ),
]

PLATFORMS_BY_NAME = {p.name: p for p in PLATFORMS}

# Selectors removed from post bodies regardless of platform.
COMMON_EXCLUDE = [".signature", ".post-signature", "[class*='signature']"]
COMMON_QUOTE_EXCLUDE = [
    "blockquote:not([class*='content' i]):not([class*='message' i]):not([class*='post' i])",
    "[class*='quote' i]:not(blockquote[class*='content' i]):not([class*='messageText'])",
]


def get_platform(name: str | None) -> PlatformSpec | None:
    if not name:
        return None
    return PLATFORMS_BY_NAME.get(name)
