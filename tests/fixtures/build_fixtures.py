"""Generate the local HTML fixture forums used by the test-suite.

Run:  python tests/fixtures/build_fixtures.py
Output: tests/fixtures/sites/<site>/... plus tests/fixtures/sites/routes.json (URL -> file mapping
served by tests/fixtures/server.py). The markup imitates the real HTML of each forum platform.
"""
from __future__ import annotations

import html
import json
import shutil
from pathlib import Path

try:
    from forum_data import BY_ID, PAGES, THREADS
except ImportError:  # imported as a package
    from .forum_data import BY_ID, PAGES, THREADS

ROOT = Path(__file__).parent / "sites"
ROUTES: dict[str, dict] = {}
LAST_PAGE = max(PAGES)


def add(route: str, filename: str, content: str, **opts) -> None:
    path = ROOT / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    ROUTES[route] = {"file": filename, **opts}


def esc(s: str) -> str:
    return html.escape(s, quote=True)


def body_html(t: dict, style: str, quote_fn, code_fn=None) -> str:
    out = []
    for b in t["blocks"]:
        kind = b[0]
        if kind == "p":
            out.append(f"<p>{b[1]}</p>" if style == "p" else f"{b[1]}<br />\n<br />\n")
        elif kind == "lines":
            inner = "<br />\n".join(b[1])
            out.append(f"<p>{inner}</p>" if style == "p" else f"{inner}<br />\n<br />\n")
        elif kind in ("ul", "ol"):
            items = "".join(f"<li>{x}</li>" for x in b[1])
            out.append(f"<{kind}>{items}</{kind}>")
        elif kind == "quote":
            out.append(quote_fn(b[1], esc(b[2])))
        elif kind == "code":
            code = esc(b[1])
            out.append(code_fn(code) if code_fn else f"<pre><code>{code}</code></pre>")
    s = "\n".join(out)
    if style == "br":
        s = s.rstrip()
        while s.endswith("<br />"):
            s = s[: -len("<br />")].rstrip()
    return s


def reply_body(t: dict, k: int, style: str, quote_fn) -> str:
    r = t["replies"][k]
    parts = []
    if r.startswith("QUOTE-OP "):
        r = r[len("QUOTE-OP "):]
        # the reply quotes the complete original post
        op = body_html({**t, "blocks": [b for b in t["blocks"] if b[0] != "quote"]}, style, lambda a, x: "")
        parts.append(quote_fn(t["author"], op))
    parts.append(f"<p>{esc(r)}</p>" if style == "p" else esc(r))
    return "\n".join(parts)


def sig(t: dict) -> str:
    return f"SIGNATURE-MARKER of {t['author']} - one day at a time"


# =========================================================================== XenForo 2
def xf2():
    base = "/xenforo2/"
    forum = "index.php?forums/recovery.2/"

    def page_url(p):
        return forum if p == 1 else f"{forum}page-{p}"

    def tl(t):
        return f"index.php?threads/{t['slug']}.{t['id']}/"

    def q(author, text):
        return (f'<blockquote class="bbCodeBlock bbCodeBlock--expandable bbCodeBlock--quote js-expandWatch">'
                f'<div class="bbCodeBlock-title"><a href="index.php?goto/post&amp;id=1" class="bbCodeBlock-sourceJump">'
                f'{esc(author)} said:</a></div><div class="bbCodeBlock-content"><div class="bbCodeBlock-expandContent '
                f'js-expandContent">{text}</div><div class="bbCodeBlock-expandLink js-expandLink"><a role="button" '
                f'tabindex="0">Click to expand...</a></div></div></blockquote>')

    def code(c):
        return (f'<div class="bbCodeBlock bbCodeBlock--screenLimited bbCodeBlock--code"><div class="bbCodeBlock-title">'
                f'Code:</div><div class="bbCodeBlock-content" dir="ltr"><pre class="bbCodeCode" dir="ltr" '
                f'data-xf-init="code-block"><code>{c}</code></pre></div></div>')

    def shell(title, template, content, head=""):
        return f"""<!DOCTYPE html>
<html id="XF" lang="en-US" dir="LTR" data-xf="2.2" data-app="public" data-template="{template}" class="has-no-js template-{template}">
<head><meta charset="utf-8" /><title>{esc(title)} | Example Recovery Forum</title>{head}</head>
<body data-template="{template}">
<div class="p-pageWrapper" id="top">
<header class="p-header" id="header"><div class="p-header-inner"><div class="p-header-content">
<div class="p-header-logo p-header-logo--image"><a href="index.php"><img src="logo.png" alt="Example Recovery Forum" /></a></div>
</div></div></header>
<div class="p-navSticky p-navSticky--primary" data-xf-init="sticky-header"><nav class="p-nav"><div class="p-nav-inner">
<ul class="p-nav-list js-offCanvasNavSource"><li><div class="p-navEl is-selected"><a href="index.php" class="p-navEl-link">Forums</a></div></li>
<li><div class="p-navEl"><a href="index.php?whats-new/" class="p-navEl-link">What's new</a></div></li>
<li><div class="p-navEl"><a href="index.php?members/" class="p-navEl-link">Members</a></div></li></ul>
<div class="p-nav-opposite"><a href="index.php?login/" class="p-navgroup-link p-navgroup-link--logIn">Log in</a>
<a href="index.php?register/" class="p-navgroup-link p-navgroup-link--register">Register</a></div>
</div></nav></div>
<div class="p-body"><div class="p-body-inner">
<ul class="p-breadcrumbs" itemscope itemtype="https://schema.org/BreadcrumbList"><li itemprop="itemListElement"><a href="index.php" itemprop="item"><span itemprop="name">Forums</span></a></li>
<li itemprop="itemListElement"><a href="{forum}" itemprop="item"><span itemprop="name">Recovery</span></a></li></ul>
<div class="p-body-main p-body-main--withSidebar">
<div class="p-body-content"><div class="p-body-pageContent">
{content}
</div></div>
<div class="p-body-sidebar"><div class="block"><div class="block-container"><h3 class="block-minorHeader">Latest posts</h3>
<ul class="block-body">{''.join(f'<li class="block-row"><a href="{tl(t)}">{esc(t["title"])}</a></li>' for t in THREADS[:5])}</ul></div></div></div>
</div></div></div>
<footer class="p-footer" id="footer"><div class="p-footer-inner"><ul class="p-footer-linkList">
<li><a href="index.php?help/terms/">Terms and rules</a></li><li><a href="index.php?help/privacy-policy/">Privacy policy</a></li></ul>
<div class="p-footer-copyright"><a href="https://xenforo.com" rel="sponsored noopener">Community platform by XenForo&reg;</a></div></div></footer>
</div></body></html>"""

    def pagenav(p):
        items = "".join(
            f'<li class="pageNav-page{" pageNav-page--current" if i == p else ""}"><a href="{page_url(i)}">{i}</a></li>'
            for i in sorted(PAGES))
        prev = f'<a href="{page_url(p - 1)}" class="pageNav-jump pageNav-jump--prev">Prev</a>' if p > 1 else ""
        nxt = f'<a href="{page_url(p + 1)}" class="pageNav-jump pageNav-jump--next">Next</a>' if p < LAST_PAGE else ""
        return (f'<div class="block-outer"><div class="block-outer-main"><nav class="pageNavWrapper pageNavWrapper--mixed">'
                f'<div class="pageNav">{prev}<ul class="pageNav-main">{items}</ul>{nxt}</div></nav></div></div>')

    def item(t):
        label = ('<a href="index.php?forums/recovery.2/&amp;prefix_id=1" class="labelLink" rel="nofollow">'
                 '<span class="label label--blue" dir="auto">Rules</span></a> ') if t["sticky"] else ""
        return f"""<div class="structItem structItem--thread{' is-prefix1' if t['sticky'] else ''} js-inlineModContainer js-threadListItem-{t['id']}" data-author="{t['author']}">
<div class="structItem-cell structItem-cell--icon"><div class="structItem-iconContainer"><a href="index.php?members/{t['author'].lower()}.{100 + t['id']}/" class="avatar avatar--s" data-user-id="{100 + t['id']}"><span class="avatar-u{100 + t['id']}-s">O</span></a></div></div>
<div class="structItem-cell structItem-cell--main" data-xf-init="touch-proxy">
<div class="structItem-title">{label}<a href="{tl(t)}" class="" data-tp-primary="on" data-xf-init="preview-tooltip" data-preview-url="index.php?threads/{t['slug']}.{t['id']}/preview">{esc(t['title'])}</a></div>
<div class="structItem-minor"><ul class="structItem-parts"><li><a href="index.php?members/{t['author'].lower()}.{100 + t['id']}/" class="username" dir="auto" data-user-id="{100 + t['id']}">{t['author']}</a></li>
<li class="structItem-startDate"><a href="{tl(t)}" rel="nofollow"><time class="u-dt" dir="auto" datetime="2024-01-{t['id']:02d}T10:00:00+0000">{t['date']}</time></a></li></ul></div>
</div>
<div class="structItem-cell structItem-cell--meta" title="First message reaction score: 3"><dl class="pairs pairs--justified"><dt>Replies</dt><dd>{len(t['replies'])}</dd></dl><dl class="pairs pairs--justified structItem-minor"><dt>Views</dt><dd>1K</dd></dl></div>
<div class="structItem-cell structItem-cell--latest"><a href="index.php?threads/{t['slug']}.{t['id']}/latest" rel="nofollow"><time class="structItem-latestDate u-dt" dir="auto">Feb 1, 2024</time></a>
<div class="structItem-minor"><a href="index.php?members/helper_a.7/" class="username" dir="auto">Helper_A</a></div></div>
</div>"""

    for p, ids in PAGES.items():
        sticky = [BY_ID[i] for i in ids if BY_ID[i]["sticky"]]
        normal = [BY_ID[i] for i in ids if not BY_ID[i]["sticky"]]
        content = f"""<div class="block" data-xf-init="inline-mod" data-type="thread">
{pagenav(p)}
<div class="block-container"><div class="block-body"><div class="structItemContainer">
<div class="structItemContainer-group structItemContainer-group--sticky">{''.join(item(t) for t in sticky)}</div>
<div class="structItemContainer-group js-threadList">{''.join(item(t) for t in normal)}</div>
</div></div></div>
{pagenav(p)}
</div>"""
        head = f'<link rel="canonical" href="http://127.0.0.1/xenforo2/{page_url(p)}" />'
        add(base + page_url(p), f"xenforo2/forum_p{p}.html", shell("Recovery", "forum_view", content, head))

    def post(t, n, author, date, body, signature=""):
        pid = t["id"] * 100 + n
        sig_html = (f'<aside class="message-signature"><div class="bbWrapper">{esc(signature)}</div></aside>'
                    if signature else "")
        return f"""<article class="message message--post js-post js-inlineModContainer" data-author="{author}" data-content="post-{pid}" id="js-post-{pid}">
<span class="u-anchorTarget" id="post-{pid}"></span>
<div class="message-inner">
<div class="message-cell message-cell--user"><section class="message-user"><div class="message-avatar"><div class="message-avatar-wrapper"><a href="index.php?members/{author.lower()}.5/" class="avatar avatar--m" data-user-id="5"><span class="avatar-u5-m">{author[0]}</span></a></div></div>
<div class="message-userDetails"><h4 class="message-name"><a href="index.php?members/{author.lower()}.5/" class="username" dir="auto" data-user-id="5">{author}</a></h4><h5 class="userTitle message-userTitle" dir="auto">Member</h5></div>
<div class="message-userExtras"><dl class="pairs pairs--justified"><dt>Joined</dt><dd>Jan 1, 2020</dd></dl><dl class="pairs pairs--justified"><dt>Messages</dt><dd>120</dd></dl></div>
<span class="message-userArrow"></span></section></div>
<div class="message-cell message-cell--main"><div class="message-main js-quickEditTarget">
<header class="message-attribution message-attribution--split"><ul class="message-attribution-main listInline"><li class="u-concealed"><a href="index.php?threads/{t['slug']}.{t['id']}/post-{pid}" rel="nofollow"><time class="u-dt" dir="auto" datetime="2024-01-01T10:00:00+0000">{date}</time></a></li></ul>
<ul class="message-attribution-opposite message-attribution-opposite--list"><li><a href="index.php?threads/{t['slug']}.{t['id']}/post-{pid}" class="message-attribution-gadget" rel="nofollow">#{n}</a></li></ul></header>
<div class="message-content js-messageContent">
<div class="message-userContent lbContainer js-lbContainer" data-lb-id="post-{pid}">
<article class="message-body js-selectToQuote"><div class="bbWrapper">{body}</div><div class="js-selectToQuoteEnd">&nbsp;</div></article>
</div>
{sig_html}
</div>
<footer class="message-footer"><div class="message-actionBar actionBar"><div class="actionBar-set actionBar-set--external">
<a href="index.php?posts/{pid}/react" class="actionBar-action actionBar-action--reaction">Like</a>
<a href="index.php?threads/{t['slug']}.{t['id']}/reply&amp;quote={pid}" class="actionBar-action actionBar-action--reply">Reply</a></div></div>
<div class="reactionsBar js-reactionsList is-active"><a class="reactionsBar-link" href="index.php?posts/{pid}/reactions">Helper_A and 2 others</a></div></footer>
</div></div></div></article>"""

    for t in THREADS:
        posts = [post(t, 1, t["author"], t["date"], body_html(t, "br", q, code), sig(t))]
        for k in range(len(t["replies"])):
            posts.append(post(t, k + 2, "Helper_A", "Feb 1, 2024", reply_body(t, k, "br", q)))
        content = f"""<div class="p-title"><h1 class="p-title-value">{esc(t['title'])}</h1></div>
<div class="p-description"><ul class="listInline listInline--bullet"><li><a href="index.php?members/x.5/" class="username u-concealed">{t['author']}</a></li><li><time>{t['date']}</time></li></ul></div>
<div class="block block--messages" data-xf-init="" data-type="post"><div class="block-container lbContainer"><div class="block-body js-replyNewMessageContainer">
{''.join(posts)}
</div></div></div>
<div class="block"><div class="block-container"><h3 class="block-header">Similar threads</h3><div class="block-body">
<div class="structItemContainer">{''.join(f'<div class="structItem"><div class="structItem-title"><a href="{tl(x)}">{esc(x["title"])}</a></div></div>' for x in THREADS[:3])}</div></div></div></div>"""
        opts = {"fail_times": 1} if t["id"] == 6 else {}
        add(base + tl(t), f"xenforo2/thread_{t['id']}.html", shell(t["title"], "thread_view", content), **opts)


# =========================================================================== XenForo 1
def xf1():
    base = "/xenforo1/"
    forum = "index.php?forums/general.3/"

    def page_url(p):
        return forum if p == 1 else f"{forum}page-{p}"

    def tl(t):
        return f"index.php?threads/{t['slug']}.{t['id']}/"

    def q(author, text):
        return (f'<div class="bbCodeBlock bbCodeQuote" data-author="{esc(author)}"><aside><div class="attribution type">'
                f'{esc(author)} said: <a href="index.php?goto/post&amp;id=1#post-1" class="AttributionLink">&uarr;</a></div>'
                f'<blockquote class="quoteContainer"><div class="quote">{text}</div><div class="quoteExpand">Click to expand...</div>'
                f'</blockquote></aside></div>')

    def shell(title, cls, content):
        return f"""<!DOCTYPE html>
<html id="XenForo" lang="en-US" dir="LTR" class="Public NoJs LoggedOut NoSidebar Responsive" xmlns:fb="http://www.facebook.com/2008/fbml">
<head><meta charset="utf-8" /><title>{esc(title)} | Example XF1 Board</title><meta name="description" content="Forum" /></head>
<body>
<div id="headerMover"><div id="headerProxy"></div>
<div id="content" class="{cls}"><div class="pageWidth"><div class="pageContent">
<div class="breadBoxTop"><nav><fieldset class="breadcrumb"><div class="boardTitle"><strong>Example XF1 Board</strong></div>
<span class="crumbs"><span class="crust homeCrumb"><a href="index.php" class="crumb"><span>Home</span></a></span><span class="crust"><a href="{forum}" class="crumb"><span>General</span></a></span></span></fieldset></nav></div>
<div class="mainContainer"><div class="mainContent">
{content}
</div></div>
</div></div></div>
<header><div id="header"><div id="logoBlock"><div class="pageWidth"><div class="pageContent"><div id="logo"><a href="index.php"><img src="logo.png" alt="Example XF1 Board" /></a></div></div></div></div>
<div id="navigation" class="pageWidth withSearch"><div class="pageContent"><nav><div class="navTabs"><ul class="publicTabs"><li class="navTab forums selected"><a href="index.php" class="navLink">Forums</a></li><li class="navTab members"><a href="index.php?members/" class="navLink">Members</a></li></ul>
<ul class="visitorTabs"><li class="navTab login"><a href="index.php?login/" class="navLink">Log in or Sign up</a></li></ul></div></nav></div></div></div></header>
</div>
<footer><div class="footer"><div class="pageWidth"><div class="pageContent"><ul class="footerLinks"><li><a href="index.php?help/terms">Terms and Rules</a></li><li><a href="index.php?misc/contact">Contact Us</a></li></ul></div></div></div>
<div class="footerLegal"><div class="pageWidth"><div class="pageContent"><div id="copyright"><a href="https://xenforo.com" class="concealed">Forum software by XenForo&trade; &copy;2010-2017 XenForo Ltd.</a></div></div></div></div></footer>
</body></html>"""

    def pagenav(p):
        links = "".join(f'<a href="{page_url(i)}" class="{"currentPage " if i == p else ""}">{i}</a>' for i in sorted(PAGES))
        prev = f'<a href="{page_url(p - 1)}" class="text">&lt; Prev</a>' if p > 1 else ""
        nxt = f'<a href="{page_url(p + 1)}" class="text">Next &gt;</a>' if p < LAST_PAGE else ""
        return (f'<div class="pageNavLinkGroup"><div class="PageNav" data-page="{p}" data-range="2" data-start="2" '
                f'data-end="{LAST_PAGE}" data-last="{LAST_PAGE}"><span class="pageNavHeader">Page {p} of {LAST_PAGE}</span>'
                f'<nav>{prev}{links}{nxt}</nav></div></div>')

    def item(t):
        return f"""<li id="thread-{t['id']}" class="discussionListItem visible {'sticky' if t['sticky'] else ''}" data-author="{t['author']}">
<div class="listBlock posterAvatar"><span class="avatarContainer"><a href="index.php?members/{t['author'].lower()}.{t['id']}/" class="avatar Av{t['id']}s" data-avatarhtml="true"><span class="img s">O</span></a></span></div>
<div class="listBlock main"><div class="titleText">{'<div class="iconKey"><span class="sticky" title="Sticky">Sticky</span></div>' if t['sticky'] else ''}
<h3 class="title"><a href="{tl(t)}" title="" class="PreviewTooltip" data-previewUrl="index.php?threads/{t['slug']}.{t['id']}/preview">{esc(t['title'])}</a></h3>
<div class="secondRow"><div class="posterDate muted"><a href="index.php?members/{t['author'].lower()}.{t['id']}/" class="username" dir="auto" title="Thread starter">{t['author']}</a><span class="startDate">, <a href="{tl(t)}" class="faint"><span class="DateTime" title="{t['date']}">{t['date']}</span></a></span></div></div>
</div></div>
<div class="listBlock stats pairsJustified" title="Members who liked the first message: 0"><dl class="major"><dt>Replies:</dt> <dd>{len(t['replies'])}</dd></dl><dl class="minor"><dt>Views:</dt> <dd>321</dd></dl></div>
<div class="listBlock lastPost"><dl class="lastPostInfo"><dt><a href="index.php?members/helper_a.7/" class="username" dir="auto">Helper_A</a></dt><dd class="muted"><a href="index.php?threads/{t['slug']}.{t['id']}/latest" class="dateTime" title="Go to last message"><span class="DateTime">Feb 1, 2024</span></a></dd></dl></div>
</li>"""

    for p, ids in PAGES.items():
        content = f"""<div class="titleBar"><h1>General</h1></div>
{pagenav(p)}
<form action="inline-mod/thread/switch" method="post" class="DiscussionList InlineModForm">
<div class="discussionList section sectionMain"><dl class="sectionHeaders"><dt class="posterAvatar"><a><span>Sort by:</span></a></dt><dd class="main"><a href="{forum}?order=title" class="title"><span>Title</span></a></dd></dl>
<ol class="discussionListItems">{''.join(item(BY_ID[i]) for i in ids)}</ol></div></form>
{pagenav(p)}"""
        add(base + page_url(p), f"xenforo1/forum_p{p}.html", shell("General", "forum_view", content))

    def post(t, n, author, date, body, signature=""):
        pid = t["id"] * 100 + n
        sig_html = (f'<div class="baseHtml signature messageText ugc"><aside><blockquote>{esc(signature)}</blockquote></aside></div>'
                    if signature else "")
        return f"""<li id="post-{pid}" class="message   " data-author="{author}">
<div class="messageUserInfo" itemscope="itemscope" itemtype="http://data-vocabulary.org/Person"><div class="messageUserBlock ">
<div class="avatarHolder"><span class="helper"></span><a href="index.php?members/{author.lower()}.5/" class="avatar Av5m" data-avatarhtml="true"><span class="img m">{author[0]}</span></a></div>
<h3 class="userText"><a href="index.php?members/{author.lower()}.5/" class="username" dir="auto" itemprop="name">{author}</a><em class="userTitle" itemprop="title">Member</em></h3>
<span class="arrow"><span></span></span></div></div>
<div class="messageInfo primaryContent"><div class="messageContent"><article>
<blockquote class="messageText SelectQuoteContainer ugc baseHtml">{body}<div class="messageTextEndMarker">&nbsp;</div></blockquote>
</article></div>
{sig_html}
<div class="messageMeta ToggleTriggerAnchor"><div class="privateControls"><span class="item muted"><span class="authorEnd"><a href="index.php?members/{author.lower()}.5/" class="username author" dir="auto">{author}</a>,</span>
<a href="index.php?threads/{t['slug']}.{t['id']}/#post-{pid}" title="Permalink" class="datePermalink"><span class="DateTime">{date}</span></a></span></div>
<div class="publicControls"><a href="index.php?threads/{t['slug']}.{t['id']}/#post-{pid}" title="Permalink" class="item muted postNumber hashPermalink">#{n}</a>
<a href="index.php?threads/{t['slug']}.{t['id']}/reply&amp;quote={pid}" data-postUrl="index.php?posts/{pid}/quote" class="ReplyQuote item control reply" title="Reply, quoting this message"><span></span>Reply</a></div></div>
<div id="likes-post-{pid}"></div></div></li>"""

    for t in THREADS:
        posts = [post(t, 1, t["author"], t["date"], body_html(t, "br", q), sig(t))]
        for k in range(len(t["replies"])):
            posts.append(post(t, k + 2, "Helper_A", "Feb 1, 2024", reply_body(t, k, "br", q)))
        content = f"""<div class="titleBar"><h1>{esc(t['title'])}</h1><p id="pageDescription" class="muted ">Discussion in '<a href="{forum}">General</a>' started by <a href="index.php?members/x.5/" class="username">{t['author']}</a>, <a href="{tl(t)}"><span class="DateTime">{t['date']}</span></a>.</p></div>
<form action="inline-mod/post/switch" method="post" class="InlineModForm section" id="InlineModForm"><ol class="messageList" id="messageList">{''.join(posts)}</ol></form>"""
        add(base + tl(t), f"xenforo1/thread_{t['id']}.html", shell(t["title"], "thread_view", content))


# =========================================================================== phpBB
def phpbb():
    base = "/phpbb/"
    per = 10

    def page_url(p):
        return "viewforum.php?f=2" if p == 1 else f"viewforum.php?f=2&start={(p - 1) * per}"

    def tl(t, sid=True):
        return f"viewtopic.php?f=2&t={t['id']}" + ("&sid=0123456789abcdef0123456789abcdef" if sid else "")

    def q(author, text):
        return (f'<blockquote><div><cite><a href="./memberlist.php?mode=viewprofile&amp;u=7">{esc(author)}</a> wrote: '
                f'<a href="./viewtopic.php?p=1#p1" aria-label="View quoted post" class="post-link"><i class="icon fa-arrow-circle-up fa-fw" aria-hidden="true"></i></a>'
                f'<span class="responsive-hide">Mon Jan 01, 2024 10:00 am</span></cite>{text}</div></blockquote>')

    def code(c):
        return (f'<div class="codebox"><p>Code: <a href="#" onclick="selectCode(this); return false;">Select all</a></p>'
                f'<pre><code>{c}</code></pre></div>')

    def shell(title, section, content):
        return f"""<!DOCTYPE html>
<html dir="ltr" lang="en-gb">
<head><meta charset="utf-8" /><title>{esc(title)} - Example phpBB Board</title></head>
<body id="phpbb" class="nojs notouch section-{section} ltr ">
<div id="wrap" class="wrap"><a id="top" class="top-anchor" accesskey="t"></a>
<div id="page-header"><div class="headerbar" role="banner"><div class="inner"><div id="site-description" class="site-description"><a id="logo" class="logo" href="./index.php" title="Board index"><span class="site_logo"></span></a><h1>Example phpBB Board</h1><p>Support community</p></div></div></div>
<div class="navbar" role="navigation"><div class="inner"><ul id="nav-main" class="nav-main linklist" role="menubar">
<li><a href="/phpbb/faq.php" rel="help" title="Frequently Asked Questions" role="menuitem"><i class="icon fa-question-circle fa-fw" aria-hidden="true"></i><span>FAQ</span></a></li>
<li class="rightside"><a href="./ucp.php?mode=login" title="Login" accesskey="x" role="menuitem"><span>Login</span></a></li>
<li class="rightside"><a href="./ucp.php?mode=register" role="menuitem"><span>Register</span></a></li></ul>
<ul id="nav-breadcrumbs" class="nav-breadcrumbs linklist navlinks" role="menubar"><li class="breadcrumbs" itemscope itemtype="https://schema.org/BreadcrumbList"><span class="crumb"><a href="./index.php" accesskey="h"><span>Board index</span></a></span><span class="crumb"><a href="./viewforum.php?f=2"><span>Recovery</span></a></span></li></ul></div></div></div>
<div id="page-body" class="page-body" role="main">
{content}
</div>
<div id="page-footer" class="page-footer" role="contentinfo"><div class="navbar" role="navigation"><div class="inner"><ul id="nav-footer" class="nav-footer linklist" role="menubar"><li class="breadcrumbs"><span class="crumb"><a href="./index.php">Board index</a></span></li><li class="rightside"><a href="./memberlist.php?mode=team">The team</a></li></ul></div></div>
<div class="copyright"><p class="footer-row"><span class="footer-copyright">Powered by <a href="https://www.phpbb.com/">phpBB</a>&reg; Forum Software &copy; phpBB Limited</span></p></div></div>
</div></body></html>"""

    def pagination(p):
        lis = []
        if p > 1:
            lis.append(f'<li class="arrow previous"><a class="button button-icon-only" href="./{page_url(p - 1)}" rel="prev" role="button"><i class="icon fa-chevron-left fa-fw" aria-hidden="true"></i><span class="sr-only">Previous</span></a></li>')
        for i in sorted(PAGES):
            lis.append(f'<li class="active"><span>{i}</span></li>' if i == p else
                       f'<li><a class="button" href="./{page_url(i)}" role="button">{i}</a></li>')
        if p < LAST_PAGE:
            lis.append(f'<li class="arrow next"><a class="button button-icon-only" href="./{page_url(p + 1)}" rel="next" role="button"><i class="icon fa-chevron-right fa-fw" aria-hidden="true"></i><span class="sr-only">Next</span></a></li>')
        return f'<div class="pagination">30 topics &bull; <ul>{"".join(lis)}</ul></div>'

    def row(t):
        return f"""<li class="row bg{1 + t['id'] % 2}{' sticky' if t['sticky'] else ''}"><dl class="row-item {'sticky_read' if t['sticky'] else 'topic_read'}">
<dt title="No unread posts"><div class="list-inner"><a href="./{tl(t).replace('&', '&amp;')}" class="topictitle">{esc(t['title'])}</a><br />
<div class="responsive-show" style="display: none;">Last post by <a href="./memberlist.php?mode=viewprofile&amp;u=7" class="username">Helper_A</a></div>
<div class="topic-poster responsive-hide left-box">by <a href="./memberlist.php?mode=viewprofile&amp;u={t['id']}" class="username">{t['author']}</a> &raquo; <time datetime="2024-01-{t['id']:02d}T10:00:00+00:00">Mon {t['date']}</time></div></div></dt>
<dd class="posts">{len(t['replies'])} <dfn>Replies</dfn></dd><dd class="views">100 <dfn>Views</dfn></dd>
<dd class="lastpost"><span><dfn>Last post </dfn>by <a href="./memberlist.php?mode=viewprofile&amp;u=7" class="username">Helper_A</a>
<a href="./viewtopic.php?p={t['id'] * 100 + 2}#p{t['id'] * 100 + 2}" title="Go to last post"><i class="icon fa-external-link-square fa-fw icon-lightgray icon-md" aria-hidden="true"></i><span class="sr-only"></span></a><br /><time>Thu Feb 01, 2024</time></span></dd></dl></li>"""

    for p, ids in PAGES.items():
        sticky = "".join(row(BY_ID[i]) for i in ids if BY_ID[i]["sticky"])
        normal = "".join(row(BY_ID[i]) for i in ids if not BY_ID[i]["sticky"])
        content = f"""<h2 class="forum-title"><a href="./viewforum.php?f=2">Recovery</a></h2>
<div class="action-bar bar-top"><a href="./posting.php?mode=post&amp;f=2" class="button" title="Post a new topic"><span>New Topic</span></a>{pagination(p)}</div>
<div class="forumbg announcement"><div class="inner"><ul class="topiclist"><li class="header"><dl class="row-item"><dt><div class="list-inner">Announcements</div></dt><dd class="posts">Replies</dd></dl></li></ul>
<ul class="topiclist topics">{sticky}</ul></div></div>
<div class="forumbg"><div class="inner"><ul class="topiclist"><li class="header"><dl class="row-item"><dt><div class="list-inner">Topics</div></dt><dd class="posts">Replies</dd></dl></li></ul>
<ul class="topiclist topics">{normal}</ul></div></div>
<div class="action-bar bar-bottom">{pagination(p)}</div>"""
        add(base + page_url(p), f"phpbb/forum_p{p}.html", shell("Recovery", "viewforum", content))

    def post(t, n, author, date, body, signature=""):
        pid = t["id"] * 100 + n
        sig_html = f'<div id="sig{pid}" class="signature">{esc(signature)}</div>' if signature else ""
        return f"""<div id="p{pid}" class="post has-profile bg{1 + n % 2}"><div class="inner">
<dl class="postprofile" id="profile{pid}"><dt class="has-profile-rank no-avatar"><div class="avatar-container"></div><a href="./memberlist.php?mode=viewprofile&amp;u=5" class="username">{author}</a></dt>
<dd class="profile-rank">Member</dd><dd class="profile-posts"><strong>Posts:</strong> <a href="./search.php?author_id=5&amp;sr=posts">12</a></dd><dd class="profile-joined"><strong>Joined:</strong> Mon Jan 06, 2020</dd></dl>
<div class="postbody"><div id="post_content{pid}">
<h3 class="{'first' if n == 1 else ''}"><a href="./viewtopic.php?p={pid}#p{pid}">{'Re: ' if n > 1 else ''}{esc(t['title'])}</a></h3>
<ul class="post-buttons"><li><a href="./posting.php?mode=quote&amp;p={pid}" title="Reply with quote" class="button button-icon-only"><i class="icon fa-quote-left fa-fw" aria-hidden="true"></i><span>Quote</span></a></li></ul>
<p class="author"><a class="unread" href="./viewtopic.php?p={pid}#p{pid}" title="Post"><i class="icon fa-file fa-fw icon-lightgray icon-md" aria-hidden="true"></i><span class="sr-only">Post</span></a><span class="responsive-hide">by <strong><a href="./memberlist.php?mode=viewprofile&amp;u=5" class="username">{author}</a></strong> &raquo; </span><time datetime="2024-01-01T10:00:00+00:00">{date}</time></p>
<div class="content">{body}</div>
{sig_html}
</div></div>
<div class="back2top"><a href="#top" class="top" title="Top"><i class="icon fa-chevron-circle-up fa-fw icon-gray" aria-hidden="true"></i><span class="sr-only">Top</span></a></div>
</div></div><hr class="divider" />"""

    for t in THREADS:
        posts = [post(t, 1, t["author"], t["date"], body_html(t, "br", q, code), sig(t))]
        for k in range(len(t["replies"])):
            posts.append(post(t, k + 2, "Helper_A", "Thu Feb 01, 2024", reply_body(t, k, "br", q)))
        n = len(posts)
        content = f"""<h2 class="topic-title"><a href="./viewtopic.php?t={t['id']}">{esc(t['title'])}</a></h2>
<div class="action-bar bar-top"><a href="./posting.php?mode=reply&amp;t={t['id']}" class="button"><span>Post Reply</span></a>
<div class="pagination">{n} post{'s' if n > 1 else ''} &bull; Page <strong>1</strong> of <strong>1</strong></div></div>
{''.join(posts)}"""
        add(base + tl(t, sid=False), f"phpbb/topic_{t['id']}.html", shell(t["title"], "viewtopic", content))


# =========================================================================== vBulletin 4
def vbulletin():
    base = "/vbulletin/"

    def page_url(p):
        return "forumdisplay.php?f=5" if p == 1 else f"forumdisplay.php?f=5&page={p}"

    def tl(t, s=True):
        return f"showthread.php?t={t['id']}" + ("&s=0123456789abcdef0123456789abcdef" if s else "")

    def q(author, text):
        return (f'<div class="bbcode_container"><div class="bbcode_quote"><div class="quote_container">'
                f'<div class="bbcode_quote_container"></div><div class="bbcode_postedby"><img title="Quote" src="images/misc/quote_icon.png" alt="Quote" /> '
                f'Originally Posted by <strong>{esc(author)}</strong><a href="showthread.php?p=1#post1" rel="nofollow"><img class="inlineimg" src="images/buttons/viewpost-right.png" alt="View Post" /></a></div>'
                f'<div class="message">{text}</div></div></div></div>')

    def code(c):
        return (f'<div class="bbcode_container"><div class="bbcode_description">Code:</div>'
                f'<pre class="bbcode_code" style="height:84px;">{c}</pre></div>')

    def shell(title, content, script):
        return f"""<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml" dir="ltr" lang="en" id="vbulletin_html">
<head><meta http-equiv="Content-Type" content="text/html; charset=UTF-8" /><meta name="generator" content="vBulletin 4.2.5" />
<title>{esc(title)}</title><script type="text/javascript">var SCRIPT = "{script}";</script></head>
<body>
<div class="above_body"><div id="header" class="floatcontainer doc_header"><div><a name="top" href="forum.php" class="logo-image"><img src="images/misc/vbulletin4_logo.png" alt="Example vBulletin" /></a></div>
<div id="toplinks" class="toplinks"><ul class="nouser"><li><a href="register.php" rel="nofollow">Register</a></li><li><a rel="help" href="faq.php">Help</a></li></ul></div></div>
<div id="navbar" class="navbar"><ul id="navtabs" class="navtabs floatcontainer"><li class="selected"><a class="navtab" href="forum.php">Forum</a></li><li><a class="navtab" href="search.php">Search</a></li><li><a class="navtab" href="memberlist.php">Members</a></li></ul></div></div>
<div class="body_wrapper">
<div id="breadcrumb" class="breadcrumb"><ul class="floatcontainer"><li class="navbithome"><a href="index.php" accesskey="1"><img src="images/misc/navbit-home.png" alt="Home" /></a></li><li class="navbit"><a href="forum.php">Forum</a></li><li class="navbit"><a href="forumdisplay.php?f=5">Recovery</a></li></ul><hr /></div>
{content}
</div>
<div id="footer" class="floatcontainer footer"><ul id="footer_links" class="footer_links"><li><a href="sendmessage.php" rel="nofollow" accesskey="9">Contact Us</a></li><li><a href="archive/index.php">Archive</a></li><li><a href="#top" onclick="document.location.hash='top'; return false;">Top</a></li></ul></div>
<div id="footer_copyright" class="shade footer_copyright">Powered by <a href="https://www.vbulletin.com" id="vbulletinlink">vBulletin&reg;</a> Version 4.2.5</div>
</body></html>"""

    def pagenav(p):
        spans = []
        if p > 1:
            spans.append(f'<span class="prev_next"><a rel="prev" href="{page_url(p - 1)}" title="Prev Page - Results"><img src="images/pagination/previous-right.png" alt="Previous" /></a></span>')
        for i in sorted(PAGES):
            spans.append(f'<span class="selected"><a href="javascript://" title="Results">{i}</a></span>' if i == p else
                         f'<span><a href="{page_url(i)}" title="Show results">{i}</a></span>')
        if p < LAST_PAGE:
            spans.append(f'<span class="prev_next"><a rel="next" href="{page_url(p + 1)}" title="Next Page - Results"><img src="images/pagination/next-right.png" alt="Next" /></a></span>')
        return (f'<div class="threadpagenav"><form action="{page_url(p)}" method="get" class="pagination popupmenu nohovermenu">'
                f'<span><a href="javascript://" class="popupctrl">Page {p} of {LAST_PAGE}</a></span>{"".join(spans)}</form></div>')

    def bit(t):
        return f"""<li class="threadbit {'sticky' if t['sticky'] else 'hot'}" id="thread_{t['id']}"><div class="rating0 {'sticky' if t['sticky'] else 'nonsticky'}">
<div class="threadinfo" title="{esc(t['title'])} preview text"><a class="threadstatus" rel="vB::AJAX"></a><div class="inner">
<h3 class="threadtitle">{'<span class="prefix understate">Sticky: </span>' if t['sticky'] else ''}<a class="title" href="{tl(t).replace('&', '&amp;')}" id="thread_title_{t['id']}">{esc(t['title'])}</a></h3>
<div class="threadmeta"><div class="author"><span class="label">Started by&nbsp;<a href="member.php?u={t['id']}" class="username understate" title="Started by {t['author']}">{t['author']}</a>,&nbsp;{t['date']}</span></div></div></div></div>
<ul class="threadstats td alt" title=""><li>Replies: <a href="misc.php?do=whoposted&amp;t={t['id']}" onclick="who({t['id']}); return false;" class="understate">{len(t['replies'])}</a></li><li>Views: 1,234</li></ul>
<dl class="threadlastpost td"><dt class="lastpostby hidden">Last Post By:</dt><dd><div class="popupmenu memberaction"><a class="username offline popupctrl" href="member.php?u=7" title="Helper_A is offline"><strong>Helper_A</strong></a></div></dd>
<dd>Feb 1, 2024, <span class="time">10:00 AM</span><a href="showthread.php?t={t['id']}&amp;p={t['id'] * 100 + 2}#post{t['id'] * 100 + 2}" class="lastpostdate understate" title="Go to last post"><img title="Go to last post" class="inlineimg" src="images/buttons/lastpost-right.png" alt="Go to last post" /></a></dd></dl>
</div></li>"""

    for p, ids in PAGES.items():
        st = "".join(bit(BY_ID[i]) for i in ids if BY_ID[i]["sticky"])
        nm = "".join(bit(BY_ID[i]) for i in ids if not BY_ID[i]["sticky"])
        content = f"""<div id="pagetitle"><h1>Forum: <span class="forumtitle">Recovery</span></h1></div>
<div id="above_threadlist" class="above_threadlist"><a href="newthread.php?do=newthread&amp;f=5" class="newcontent_textcontrol" rel="nofollow">Post New Thread</a>{pagenav(p)}</div>
<div id="threadlist" class="threadlist"><form id="thread_inlinemod_form" action="inlinemod.php?forumid=5" method="post"><div>
<div class="threadlisthead table"><div><span class="threadinfo"><span class="threadtitle"><a href="forumdisplay.php?f=5&amp;sort=title&amp;order=asc" rel="nofollow">Title</a></span></span></div></div>
<ol id="stickies" class="stickies">{st}</ol><ol id="threads" class="threads">{nm}</ol></div></form></div>
<div id="below_threadlist" class="noinlinemod below_threadlist">{pagenav(p)}</div>"""
        add(base + page_url(p), f"vbulletin/forum_p{p}.html", shell("Recovery", content, "forumdisplay"))

    def post(t, n, author, date, body, signature=""):
        pid = t["id"] * 100 + n
        sig_html = (f'<div class="after_content"><blockquote class="signature restore"><div class="signaturecontainer">{esc(signature)}</div></blockquote></div>'
                    if signature else "")
        return f"""<li class="postbitlegacy postbitim postcontainer old" id="post_{pid}">
<div class="posthead"><span class="postdate old"><span class="date">{date},&nbsp;<span class="time">10:00 AM</span></span></span>
<span class="nodecontrols"><a name="post{pid}" href="showthread.php?t={t['id']}&amp;p={pid}#post{pid}" class="postcounter">#{n}</a><a id="postcount{pid}" name="{n}"></a></span></div>
<div class="postdetails"><div class="userinfo"><div class="username_container"><div class="popupmenu memberaction"><a class="username offline popupctrl" href="member.php?u=5" title="{author} is offline"><strong>{author}</strong></a></div>
<img class="inlineimg onlinestatus" src="images/statusicon/user-offline.png" alt="{author} is offline" /></div><span class="usertitle">Junior Member</span>
<hr /><dl class="userinfo_extra"><dt>Join Date</dt> <dd>Jan 2020</dd><dt>Posts</dt> <dd>12</dd></dl></div>
<div class="postbody"><div class="postrow has_after_content"><h2 class="title icon">{esc(t['title']) if n == 1 else ''}</h2>
<div class="content"><div id="post_message_{pid}"><blockquote class="postcontent restore ">{body}</blockquote></div></div></div>
{sig_html}
<div class="cleardiv"></div></div></div>
<div class="postfoot"><div class="textcontrols floatcontainer"><span class="postcontrols"><img style="display:none" id="progress_{pid}" src="images/misc/progress.gif" alt="" />
<a id="qrwq_{pid}" class="newreply" href="newreply.php?do=newreply&amp;p={pid}" rel="nofollow" title="Reply With Quote"><img id="quoteimg_{pid}" src="clear.gif" alt="Reply With Quote" /> Reply With Quote</a></span></div></div>
<hr /></li>"""

    for t in THREADS:
        posts = [post(t, 1, t["author"], t["date"], body_html(t, "br", q, code), sig(t))]
        for k in range(len(t["replies"])):
            posts.append(post(t, k + 2, "Helper_A", "Feb 1, 2024", reply_body(t, k, "br", q)))
        content = f"""<div id="pagetitle" class="pagetitle"><h1>Thread: <span class="threadtitle"><a href="showthread.php?t={t['id']}" title="Reload this Page">{esc(t['title'])}</a></span></h1></div>
<div id="postlist" class="postlist restrain"><ol id="posts" class="posts" start="1">{''.join(posts)}</ol></div>
<div id="similar_threads"><h4 class="threadinfohead blockhead">Similar Threads</h4><div class="blockbody"><ol class="similar_threads">{''.join(f'<li class="floatcontainer"><h6><a href="showthread.php?t={x["id"]}" title="">{esc(x["title"])}</a></h6></li>' for x in THREADS[:3])}</ol></div></div>"""
        add(base + tl(t, s=False), f"vbulletin/thread_{t['id']}.html", shell(t["title"], content, "showthread"))


# =========================================================================== Discourse
def discourse():
    base = "/discourse/"
    cat = "c/general/5"

    def page_url(p):  # Discourse pages are 0-based ?page=N
        return cat if p == 1 else f"{cat}?page={p - 1}"

    def tl(t):
        return f"t/{t['slug']}/{t['id']}"

    def q(author, text):
        return (f'<aside class="quote no-group" data-username="{esc(author)}" data-post="1" data-topic="1">'
                f'<div class="title"><div class="quote-controls"></div><img alt="" width="24" height="24" src="/a.png" class="avatar"> {esc(author)}:</div>'
                f'<blockquote>{text}</blockquote></aside>')

    def shell(title, content):
        return f"""<!DOCTYPE html>
<html lang="en" class="desktop-view not-mobile-device text-size-normal anon">
<head><meta charset="utf-8"><title>{esc(title)} - Example Discourse</title>
<meta name="generator" content="Discourse 3.2.0 - https://github.com/discourse/discourse version 0123abcd">
<meta name="discourse_theme_id" content="2"><meta name="discourse_current_homepage" content="latest"></head>
<body class="docked">
<section id="main" class="ember-application">
<div class="d-header-wrap"><header class="d-header clearfix"><div class="wrap"><div class="contents"><div class="home-logo-wrapper-outlet"><div class="title"><a href="/discourse/"><img src="/logo.png" alt="Example Discourse" id="site-logo" class="logo-big"></a></div></div>
<div class="panel clearfix" role="navigation"><span class="header-buttons"><button class="btn btn-small sign-up-button">Sign Up</button><button class="btn btn-small login-button">Log In</button></span></div></div></div></header></div>
<div id="main-outlet-wrapper" class="wrap" role="main">
<div class="sidebar-wrapper"><section class="sidebar-container"><div class="sidebar-sections"><a class="sidebar-section-link" href="/discourse/latest">Topics</a><a class="sidebar-section-link" href="/discourse/u">Users</a><a class="sidebar-section-link" href="/discourse/about">About</a></div></section></div>
<div id="main-outlet">
{content}
</div></div></section></body></html>"""

    def row(t):
        return f"""<tr data-topic-id="{t['id']}" class="topic-list-item category-general{' pinned' if t['sticky'] else ''} visited ember-view">
<td class="main-link clearfix topic-list-data" colspan="1"><span class="link-top-line"><a href="/discourse/{tl(t)}" level="2" class="title raw-link raw-topic-link" data-topic-id="{t['id']}">{esc(t['title'])}</a></span>
<div class="link-bottom-line"><a class="badge-category__wrapper" href="/discourse/{cat}"><span class="badge-category__name">General</span></a><div class="discourse-tags"></div></div></td>
<td class="posters topic-list-data"><a href="/discourse/u/{t['author']}" data-user-card="{t['author']}" class="latest single"><img alt="" width="25" height="25" src="/a.png" class="avatar latest single" title="{t['author']} - Original Poster"></a></td>
<td class="num posts-map posts topic-list-data"><a href="/discourse/{tl(t)}/1" class="badge-posts"><span aria-label="This topic has {len(t['replies'])} replies" class="number">{len(t['replies'])}</span></a></td>
<td class="num views topic-list-data"><span class="number" title="this topic has been viewed 100 times">100</span></td>
<td class="num topic-list-data age activity" title="First post: Jan 1, 2024"><a class="post-activity" href="/discourse/{tl(t)}/{len(t['replies']) + 1}"><span class="relative-date" data-time="1704103200000">2d</span></a></td></tr>"""

    pages = dict(PAGES)
    pages[LAST_PAGE + 1] = []  # Discourse returns an empty list past the end
    for p, ids in pages.items():
        rows = "".join(row(BY_ID[i]) for i in ids)
        body = (f'<table class="topic-list ember-view"><thead class="topic-list-header"><tr><th data-sort-order="default" class="default">Topic</th><th class="posters">Users</th>'
                f'<th class="posts num">Replies</th><th class="views num">Views</th><th class="activity num">Activity</th></tr></thead>'
                f'<tbody class="topic-list-body">{rows}</tbody></table>') if ids else \
            '<div class="topic-list-bottom"><h3>There are no more General topics.</h3></div>'
        content = f"""<div class="category-heading"><h1 class="category-title">General</h1></div>
<div class="list-controls"><div class="container"><section class="navigation-container"><ul id="navigation-bar" class="nav nav-pills"><li class="active"><a href="/discourse/{cat}/l/latest">Latest</a></li><li><a href="/discourse/{cat}/l/top">Top</a></li></ul></section></div></div>
<div class="container list-container"><div class="row"><div class="full-width"><div id="list-area">{body}</div></div></div></div>"""
        add(base + page_url(p), f"discourse/category_p{p}.html", shell("General", content))

    def post(t, n, author, date, body):
        return f"""<div class="topic-post clearfix {'topic-owner' if n == 1 else ''} regular" data-post-number="{n}">
<article id="post_{n}" aria-label="post #{n} by @{author}" role="region" data-post-id="{t['id'] * 100 + n}" data-topic-id="{t['id']}" data-user-id="5" class="boxed onscreen-post">
<div class="row"><div class="topic-avatar"><div class="post-avatar"><a class="trigger-user-card main-avatar" href="/discourse/u/{author}" aria-hidden="true" data-user-card="{author}"><img alt="" width="48" height="48" src="/a.png" class="avatar"></a></div></div>
<div class="topic-body clearfix"><div role="heading" aria-level="2" class="topic-meta-data"><div class="names trigger-user-card"><span class="first username"><a href="/discourse/u/{author}" data-user-card="{author}">{author}</a></span></div>
<div class="post-infos"><div class="post-info post-date"><a class="post-date" href="/discourse/{tl(t)}/{n}" data-share-url="/discourse/{tl(t)}/{n}" data-post-number="{n}"><span title="{date} 10:00 am" data-time="1704103200000" class="relative-date">{date}</span></a></div></div></div>
<div class="regular contents"><div class="cooked">{body}</div>
<section class="post-menu-area clearfix"><nav class="post-controls"><div class="actions"><button class="widget-button btn-flat toggle-like like no-text btn-icon" title="like this post"><span aria-hidden="true">Like</span></button><button class="widget-button btn-flat share no-text btn-icon" title="copy a link to this post"></button><button class="widget-button btn-flat reply create fade-out btn-icon-text" title="begin composing a reply to this post"><span class="d-button-label">Reply</span></button></div></nav></section></div>
</div></div></article></div>"""

    for t in THREADS:
        posts = [post(t, 1, t["author"], t["date"], body_html(t, "p", q))]
        for k in range(len(t["replies"])):
            posts.append(post(t, k + 2, "Helper_A", "Feb 1", reply_body(t, k, "p", q)))
        content = f"""<div class="container posts"><div id="topic-title"><div class="container"><div class="title-wrapper"><h1 data-topic-id="{t['id']}"><a href="/discourse/{tl(t)}" class="fancy-title">{esc(t['title'])}</a></h1>
<div class="topic-category"><a class="badge-category__wrapper" href="/discourse/{cat}"><span class="badge-category__name">General</span></a></div></div></div></div>
<div class="row"><section class="topic-area" id="topic" data-topic-id="{t['id']}"><div class="posts-wrapper"><div class="post-stream">{''.join(posts)}</div></div></section></div>
<div id="suggested-topics" class="more-topics__list"><h3 class="suggested-topics-title">Suggested Topics</h3><table class="topic-list">{''.join(f'<tr class="topic-list-item"><td class="main-link"><a href="/discourse/{tl(x)}" class="title raw-link raw-topic-link">{esc(x["title"])}</a></td></tr>' for x in THREADS[:3])}</table></div></div>"""
        add(base + tl(t), f"discourse/topic_{t['id']}.html", shell(t["title"], content))


# =========================================================================== NodeBB
def nodebb():
    base = "/nodebb/"
    cat = "category/2/general-discussion"

    def page_url(p):
        return cat if p == 1 else f"{cat}?page={p}"

    def tl(t):
        return f"topic/{t['id']}/{t['slug']}"

    def q(author, text):
        return f'<blockquote><p><a href="/nodebb/user/{author.lower()}">@{esc(author)}</a> said:</p>\n{text}</blockquote>'

    def shell(title, cls, content):
        return f"""<!DOCTYPE html>
<html lang="en-GB" data-dir="ltr" style="direction: ltr;">
<head><title>{esc(title)} | Example NodeBB</title><meta name="viewport" content="width&#x3D;device-width, initial-scale&#x3D;1.0" /><meta name="content-type" content="text/html; charset=UTF-8" /></head>
<body class="{cls} skin-noskin">
<nav component="sidebar/left" class="d-flex text-dark bg-light sidebar sidebar-left start-0 border-end vh-100 d-none d-lg-flex flex-column justify-content-between sticky-top">
<ul id="main-nav" class="list-unstyled d-flex flex-column w-100 gap-2 mt-2"><li class="nav-item mx-2"><a class="nav-link navigation-link d-flex gap-2" href="/nodebb/categories"><span class="nav-text">Categories</span></a></li>
<li class="nav-item mx-2"><a class="nav-link navigation-link" href="/nodebb/recent"><span class="nav-text">Recent</span></a></li><li class="nav-item mx-2"><a class="nav-link navigation-link" href="/nodebb/users"><span class="nav-text">Users</span></a></li>
<li class="nav-item mx-2"><a class="nav-link" href="/nodebb/login"><span>Log in</span></a></li></ul></nav>
<main id="panel" class="d-flex flex-column gap-3 flex-grow-1 mt-3" style="min-width: 0;">
<div class="container-lg px-md-4 d-flex flex-column gap-3 h-100 mb-5 mb-lg-0" id="content">
<ol class="breadcrumb mb-0" itemscope="itemscope" itemprop="breadcrumb" itemtype="http://schema.org/BreadcrumbList"><li itemscope="itemscope" itemprop="itemListElement" class="breadcrumb-item"><a href="/nodebb/categories" itemprop="item"><span itemprop="name">Home</span></a></li></ol>
{content}
</div></main>
<!-- Powered by NodeBB -->
</body></html>"""

    def pagination(p):
        lis = []
        if p > 1:
            lis.append(f'<li class="page-item previous float-start"><a class="page-link" href="?page={p - 1}" data-page="{p - 1}"><i class="fa fa-chevron-left"></i></a></li>')
        for i in sorted(PAGES):
            lis.append(f'<li class="page-item{" active" if i == p else ""}"><a class="page-link" href="?page={i}" data-page="{i}">{i}</a></li>')
        if p < LAST_PAGE:
            lis.append(f'<li class="page-item next float-end" component="pagination/next"><a class="page-link" href="?page={p + 1}" data-page="{p + 1}"><i class="fa fa-chevron-right"></i></a></li>')
        return f'<div component="pagination" class="pagination-container mt-3"><ul class="pagination hidden-xs justify-content-center">{"".join(lis)}</ul></div>'

    def item(t, idx):
        return f"""<li component="category/topic" class="category-item hover-parent border-bottom py-3 py-lg-4 d-flex flex-column flex-lg-row align-items-start{' pinned' if t['sticky'] else ''}" data-tid="{t['id']}" data-index="{idx}" data-cid="2" itemprop="itemListElement" itemscope itemtype="https://schema.org/ListItem">
<meta itemprop="name" content="{esc(t['title'])}"><meta itemprop="position" content="{idx}"><link itemprop="url" content="/nodebb/{tl(t)}">
<div class="d-flex p-0 col-12 col-lg-7 gap-2 gap-lg-3 pe-1 align-items-start"><div class="flex-shrink-0 position-relative"><a class="d-inline-block text-decoration-none avatar-tooltip" title="{t['author']}" href="/nodebb/user/{t['author'].lower()}"><span title="{t['author']}" class="avatar avatar-rounded" style="--avatar-size: 40px;">O</span></a></div>
<div class="flex-grow-1 d-flex flex-wrap gap-1 position-relative"><h3 component="topic/header" class="title text-break fs-5 fw-semibold m-0 tracking-tight w-100"><a class="text-reset" href="/nodebb/{tl(t)}">{esc(t['title'])}</a></h3>
<span class="timeago text-muted text-xs" title="2024-01-{t['id']:02d}T10:00:00.000Z">{t['date']}</span></div></div>
<div class="d-flex p-0 col-lg-5 col-12 align-content-stretch"><div class="meta stats d-none d-lg-grid col-6 gap-1 pe-2 text-muted" style="grid-template-columns: 1fr 1fr 1fr;"><div class="stats-votes overflow-hidden d-flex flex-column align-items-center"><span class="fs-4" title="0">0</span><span class="d-none d-xl-flex text-uppercase text-xs">votes</span></div>
<div class="stats-postcount overflow-hidden d-flex flex-column align-items-center"><span class="fs-4" title="{len(t['replies']) + 1}">{len(t['replies']) + 1}</span><span class="d-none d-xl-flex text-uppercase text-xs">posts</span></div></div>
<div component="topic/teaser" class="meta teaser col-lg-6 col-12 d-none d-lg-block"><div class="lastpost border-start border-4 lh-sm h-100" style="border-color: #59b3d0!important;"><div class="ps-2 text-xs"><a href="/nodebb/user/helper_a" class="text-decoration-none">Helper_A</a>
<a class="permalink text-muted timeago text-xs" href="/nodebb/{tl(t)}/{len(t['replies']) + 1}" title="2024-02-01">Feb 1, 2024</a></div></div></div></div>
</li>"""

    for p, ids in PAGES.items():
        content = f"""<div class="category-header d-flex flex-column gap-2"><h1 class="tracking-tight fs-2 fw-semibold mb-0">General Discussion</h1><div class="description text-muted text-sm w-100"><p>A place to talk about whatever you want</p></div></div>
<div class="row flex-fill mt-3"><div class="category col-12"><ul component="category" class="topics-list list-unstyled" itemscope itemtype="http://www.schema.org/ItemList" data-nextstart="20" data-set="cid:2:tids">
{''.join(item(BY_ID[i], k) for k, i in enumerate(ids))}</ul>{pagination(p)}</div></div>"""
        add(base + page_url(p), f"nodebb/category_p{p}.html", shell("General Discussion", "page-category page-category-2", content))

    def post(t, idx, author, date, body, signature=""):
        pid = t["id"] * 100 + idx + 1
        sig_html = f'<div component="post/signature" data-uid="5" class="text-xs text-muted mt-2">{esc(signature)}</div>' if signature else ""
        return f"""<li component="post" class="pt-4 {'topic-owner-post' if idx == 0 else ''}" data-index="{idx}" data-pid="{pid}" data-uid="5" data-timestamp="1704103200000" data-username="{author}" data-userslug="{author.lower()}" itemprop="comment" itemtype="http://schema.org/Comment" itemscope>
<a component="post/anchor" data-index="{idx}" id="{idx + 1}"></a><meta itemprop="datePublished" content="2024-01-01T10:00:00.000Z">
<div class="d-flex align-items-start gap-3"><div class="bg-body d-none d-sm-block rounded-circle" style="outline: 2px solid var(--bs-body-bg);"><a class="d-inline-block position-relative text-decoration-none" href="/nodebb/user/{author.lower()}" aria-label="Profile page for user {author}"><span class="avatar">{author[0]}</span></a></div>
<div class="post-container d-flex flex-grow-1 flex-column w-100" style="min-width:0;">
<div class="d-flex align-items-center gap-1 flex-wrap w-100 post-header mt-1" itemprop="author" itemscope itemtype="https://schema.org/Person"><meta itemprop="name" content="{author}">
<a class="fw-bold text-nowrap text-truncate" href="/nodebb/user/{author.lower()}" data-username="{author}" data-uid="5">{author}</a>
<div class="d-flex gap-1 align-items-center"><span class="text-muted">wrote</span><a href="/nodebb/post/{pid}" class="timeago text-muted" title="2024-01-01T10:00:00.000Z">{date}</a></div></div>
<div class="content text-break" component="post/content" itemprop="text">{body}</div>
{sig_html}
<div class="d-flex"><div component="post/actions" class="d-flex flex-grow-1 align-items-center justify-content-end gap-1 post-tools"><a component="post/reply" href="#" class="btn btn-ghost btn-sm" title="Reply">Reply</a><a component="post/quote" href="#" class="btn btn-ghost btn-sm" title="Quote">Quote</a></div></div>
</div></div></li>"""

    for t in THREADS:
        posts = [post(t, 0, t["author"], t["date"], body_html(t, "p", q), sig(t))]
        for k in range(len(t["replies"])):
            posts.append(post(t, k + 1, "Helper_A", "Feb 1, 2024", reply_body(t, k, "p", q)))
        content = f"""<div class="topic d-flex flex-column gap-3"><div class="topic-header sticky-top mb-3 bg-body"><h1 component="post/header" class="tracking-tight fw-semibold fs-3 mb-0 text-break"><span class="topic-title" component="topic/title">{esc(t['title'])}</span></h1></div>
<ul component="topic" class="posts timeline mt-sm-4 p-0 py-3" data-tid="{t['id']}" data-cid="2">{''.join(posts)}</ul></div>"""
        add(base + tl(t), f"nodebb/topic_{t['id']}.html", shell(t["title"], f"page-topic page-topic-{t['id']}", content))


# =========================================================================== Flarum (JS-rendered DOM + noscript fallback)
def flarum():
    base = "/flarum/"
    tag = "t/general"

    def page_url(p):
        return tag if p == 1 else f"{tag}?page={p}"

    def tl(t):
        return f"d/{t['id']}-{t['slug']}"

    def q(author, text):
        return f'<blockquote><p><a href="/flarum/d/1/1" class="PostMention" data-id="1">{esc(author)}</a> said:</p>{text}</blockquote>'

    def shell(title, content, noscript):
        return f"""<!doctype html>
<html dir="ltr" lang="en"><head><meta charset="utf-8"><title>{esc(title)} - Example Flarum</title>
<link rel="stylesheet" href="/flarum/assets/forum.css"></head>
<body>
<div id="app" class="App affixed">
<div id="app-navigation" class="App-navigation"></div>
<div id="drawer" class="App-drawer"><header id="header" class="App-header"><div id="header-navigation" class="Header-navigation"></div>
<div class="container"><div class="Header-title"><a href="/flarum/" id="home-link">Example Flarum</a></div>
<div id="header-secondary" class="Header-secondary"><ul class="Header-controls"><li class="item-signUp"><button class="Button Button--link" type="button"><span class="Button-label">Sign Up</span></button></li><li class="item-logIn"><button class="Button Button--link" type="button"><span class="Button-label">Log In</span></button></li></ul></div></div></header></div>
<main class="App-content"><div id="content">{content}</div></main>
</div>
<div id="flarum-loading" style="display: none">Loading...</div>
<noscript><div class="Alert"><div class="container">This site is best viewed in a modern browser with JavaScript enabled.</div></div></noscript>
<div id="flarum-loading-error" style="display: none"><div class="Alert"><div class="container">Something went wrong while trying to load the full version of this site.</div></div></div>
<noscript id="flarum-content">{noscript}</noscript>
</body></html>"""

    def item(t):
        return f"""<li data-id="{t['id']}"><div class="DiscussionListItem Slidable"><div class="DiscussionListItem-content Slidable-content read">
<a href="/flarum/u/{t['author']}" class="DiscussionListItem-author" title=""><span class="Avatar">O</span></a>
<ul class="DiscussionListItem-badges badges">{'<li class="item-sticky"><div class="Badge Badge--sticky" title="Sticky"></div></li>' if t['sticky'] else ''}</ul>
<a href="/flarum/{tl(t)}" class="DiscussionListItem-main"><h3 class="DiscussionListItem-title">{esc(t['title'])}</h3>
<ul class="DiscussionListItem-info"><li class="item-tags"><span class="TagsLabel"><span class="TagLabel"><span class="TagLabel-text"><span class="TagLabel-name">General</span></span></span></span></li>
<li class="item-terminalPost"><span><span class="username">Helper_A</span> replied <time pubdate datetime="2024-02-01T10:00:00+00:00" title="Thursday, February 1, 2024 10:00 AM">Feb 1</time></span></li></ul></a>
<span class="DiscussionListItem-count" aria-hidden="true">{len(t['replies'])}</span></div></div></li>"""

    for p, ids in PAGES.items():
        items = "".join(item(BY_ID[i]) for i in ids)
        content = f"""<div class="TagHero Hero"><div class="container"><div class="containerNarrow"><h2 class="Hero-title">General</h2></div></div></div>
<div class="IndexPage"><div class="container"><div class="sideNavContainer"><nav class="IndexPage-nav sideNav"><ul><li class="item-allDiscussions"><a href="/flarum/" class="hasIcon">All Discussions</a></li><li class="item-tags"><a href="/flarum/tags">Tags</a></li></ul></nav>
<div class="IndexPage-results sideNavOffset"><div class="IndexPage-toolbar"></div><div class="DiscussionList"><ul role="feed" aria-busy="false" class="DiscussionList-discussions">{items}</ul>
<div class="DiscussionList-loadMore"><button class="Button" type="button"><span class="Button-label">Load More</span></button></div></div></div></div></div></div>"""
        prev = f'<a href="/flarum/{page_url(p - 1)}">&laquo; Previous Page</a>' if p > 1 else ""
        nxt = f'<a href="/flarum/{page_url(p + 1)}">Next Page &raquo;</a>' if p < LAST_PAGE else ""
        noscript = (f'<div class="container"><h2>General</h2><ul>'
                    + "".join(f'<li><a href="/flarum/{tl(BY_ID[i])}">{esc(BY_ID[i]["title"])}</a></li>' for i in ids)
                    + f"</ul>{prev}{nxt}</div>")
        add(base + page_url(p), f"flarum/tag_p{p}.html", shell("General", content, noscript))

    def post(t, n, author, date, body):
        return f"""<div class="PostStream-item" data-index="{n - 1}" data-time="2024-01-01T10:00:00+00:00" data-number="{n}" data-id="{t['id'] * 100 + n}" data-type="comment">
<article class="Post CommentPost{' Post--by-start-user' if n == 1 else ''}"><div><header class="Post-header"><ul><li class="item-user"><div class="PostUser"><h3 class="PostUser-name"><a href="/flarum/u/{author}"><span class="Avatar PostUser-avatar">{author[0]}</span><span class="username">{author}</span></a></h3></div></li>
<li class="item-meta"><div class="Dropdown PostMeta"><a class="Dropdown-toggle" data-toggle="dropdown"><time pubdate datetime="2024-01-01T10:00:00+00:00" title="{date}" data-humantime>{date}</time></a></div></li></ul></header>
<div class="Post-body">{body}</div>
<aside class="Post-footer"><ul><li class="item-replies"><div class="Post-mentionedBy"><span class="Post-mentionedBy-summary">Helper_A replied to this.</span></div></li></ul></aside>
<aside class="Post-actions"><ul><li class="item-reply"><button class="Button Button--link" type="button"><span class="Button-label">Reply</span></button></li></ul></aside></div></article></div>"""

    for t in THREADS:
        op = body_html(t, "p", q)
        posts = [post(t, 1, t["author"], t["date"], op)]
        ns = [f'<article><div class="PostUser"><h3 class="PostUser-name">{t["author"]}</h3></div><div class="Post-body">{op}</div></article>']
        for k in range(len(t["replies"])):
            rb = reply_body(t, k, "p", q)
            posts.append(post(t, k + 2, "Helper_A", "Feb 1, 2024", rb))
            ns.append(f'<hr><article><div class="PostUser"><h3 class="PostUser-name">Helper_A</h3></div><div class="Post-body">{rb}</div></article>')
        content = f"""<div class="DiscussionPage"><div class="DiscussionPage-discussion"><header class="Hero DiscussionHero"><div class="container"><ul class="DiscussionHero-items"><li class="item-title"><h1 class="DiscussionHero-title">{esc(t['title'])}</h1></li></ul></div></header>
<div class="container"><nav class="DiscussionPage-nav"><ul><li class="item-controls"><button class="Button">Reply</button></li></ul></nav>
<div class="DiscussionPage-stream"><div class="PostStream" role="feed" aria-live="off" aria-busy="false">{''.join(posts)}</div></div></div></div></div>"""
        noscript = f'<div class="container"><h1>{esc(t["title"])}</h1><div>{"".join(ns)}</div></div>'
        add(base + tl(t), f"flarum/d_{t['id']}.html", shell(t["title"], content, noscript))


# =========================================================================== Vanilla
def vanilla():
    base = "/vanilla/"
    cat = "categories/general"

    def page_url(p):
        return cat if p == 1 else f"{cat}/p{p}"

    def tl(t):
        return f"discussion/{t['id']}/{t['slug']}"

    def q(author, text):
        return (f'<blockquote class="Quote UserQuote blockquote"><div class="blockquote-content">'
                f'<a rel="nofollow" href="/vanilla/profile/{esc(author)}">{esc(author)}</a> said:{text}</div></blockquote>')

    def shell(body_id, cls, title, content):
        return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>{esc(title)} — Example Vanilla</title></head>
<body id="{body_id}" class="Vanilla {cls} isDesktop index">
<div id="Frame"><div class="Top"><div class="Row"><a href="/vanilla/" class="Home">Example Vanilla</a><div class="MeBox MeBox-SignIn"><div class="SignInLinks"><a href="/vanilla/entry/signin" class="SignInPopup" rel="nofollow">Sign In</a> · <a href="/vanilla/entry/register" class="ApplyButton" rel="nofollow">Register</a></div></div></div></div>
<div class="Banner"><ul><li><a href="/vanilla/discussions">Discussions</a></li><li><a href="/vanilla/activity">Activity</a></li></ul></div>
<div id="Body"><div class="Row"><div class="BreadcrumbsWrapper"><span class="Breadcrumbs" itemscope itemtype="http://schema.org/BreadcrumbList"><span class="CrumbLabel HomeCrumb"><a href="/vanilla/"><span>Home</span></a></span><span class="Crumb">›</span><span class="CrumbLabel Category-general"><a href="/vanilla/{cat}"><span>General</span></a></span></span></div>
<aside class="Column PanelColumn" id="Panel"><div class="Box BoxCategories"><h4>Categories</h4><ul class="PanelInfo PanelCategories"><li class="ClearFix"><a href="/vanilla/{cat}" class="ItemLink">General</a></li><li><a href="/vanilla/categories/offtopic" class="ItemLink">Off Topic</a></li></ul></div></aside>
<div class="Column ContentColumn" id="Content">{content}</div></div></div>
<div id="Foot"><div class="Row"><a href="https://vanillaforums.com" class="PoweredByVanilla" title="Community Software by Vanilla Forums">Forum Software Powered by Vanilla</a></div></div>
</div></body></html>"""

    def pager(p):
        links = []
        if p > 1:
            links.append(f'<a href="/vanilla/{page_url(p - 1)}" class="Previous" rel="prev" aria-label="Previous page">«</a>')
        for i in sorted(PAGES):
            links.append(f'<a href="/vanilla/{cat}/p{i}" class="{"Highlight" if i == p else ""}" aria-label="Page {i}">{i}</a>')
        if p < LAST_PAGE:
            links.append(f'<a href="/vanilla/{page_url(p + 1)}" class="Next" rel="next" aria-label="Next page">»</a>')
        return f'<span id="PagerAfter" class="Pager NumberedPager">{"".join(links)}</span>'

    def item(t):
        return f"""<li id="Discussion_{t['id']}" class="Item ItemDiscussion{' Announcement' if t['sticky'] else ''} Read"><span class="Options"></span>
<div class="ItemContent Discussion"><div class="Title" role="heading" aria-level="3"><a href="/vanilla/{tl(t)}">{esc(t['title'])}</a></div>
<div class="Meta Meta-Discussion">{'<span class="Tag Tag-Announcement">Announcement</span>' if t['sticky'] else ''}<span class="MItem MCount ViewCount"><span class="Number">100</span> views</span><span class="MItem MCount CommentCount"><span class="Number">{len(t['replies'])}</span> comments</span>
<span class="MItem LastCommentBy">Most recent by <a href="/vanilla/profile/Helper_A">Helper_A</a></span><span class="MItem LastCommentDate"><time title="February 1, 2024 10:00AM" datetime="2024-02-01T10:00:00+00:00">February 1</time></span></div></div></li>"""

    for p, ids in PAGES.items():
        content = f"""<h1 class="H HomepageTitle">General</h1>
<div class="PageControls Top"><span id="PagerBefore" class="Pager NumberedPager">{pager(p)}</span></div>
<h2 class="sr-only">Discussion List</h2><ul class="DataList Discussions">{''.join(item(BY_ID[i]) for i in ids)}</ul>
<div class="PageControls Bottom">{pager(p)}</div>"""
        html_doc = shell("vanilla_categories_index", "Categories Section-Category-general", "General", content)
        add(base + page_url(p), f"vanilla/cat_p{p}.html", html_doc)
        if p == 1:
            ROUTES[base + f"{cat}/p1"] = {"file": "vanilla/cat_p1.html"}

    def comment(t, k):
        cid = t["id"] * 100 + k
        return f"""<li class="Item ItemComment" id="Comment_{cid}"><div class="Comment"><div class="Item-Header CommentHeader"><div class="AuthorWrap"><span class="Author"><a title="Helper_A" href="/vanilla/profile/Helper_A" class="PhotoWrap"><span class="ProfilePhoto">H</span></a><a href="/vanilla/profile/Helper_A" class="Username">Helper_A</a></span></div>
<div class="Meta CommentMeta CommentInfo"><span class="MItem DateCreated"><a href="/vanilla/discussion/comment/{cid}/#Comment_{cid}" class="Permalink" rel="nofollow"><time title="February 1, 2024 10:00AM" datetime="2024-02-01T10:00:00+00:00">February 1</time></a></span></div></div>
<div class="Item-BodyWrap"><div class="Item-Body"><div class="Message userContent">{reply_body(t, k, 'p', q)}</div><div class="Reactions"><a href="#" class="ReactButton">Like</a></div></div></div></div></li>"""

    for t in THREADS:
        comments = "".join(comment(t, k) for k in range(len(t["replies"])))
        content = f"""<div class="MessageList Discussion"><div class="PageTitle"><h1>{esc(t['title'])}</h1></div>
<div id="Discussion_{t['id']}" class="Item ItemDiscussion"><div class="Discussion"><div class="Item-Header DiscussionHeader"><div class="AuthorWrap"><span class="Author"><a title="{t['author']}" href="/vanilla/profile/{t['author']}" class="PhotoWrap"><span class="ProfilePhoto">O</span></a><a href="/vanilla/profile/{t['author']}" class="Username">{t['author']}</a></span><span class="AuthorInfo"><span class="MItem RoleTitle">Member</span></span></div>
<div class="Meta DiscussionMeta"><span class="MItem DateCreated"><a href="/vanilla/{tl(t)}" class="Permalink" rel="nofollow"><time title="{t['date']}" datetime="2024-01-01T10:00:00+00:00">{t['date']}</time></a></span><span class="MItem Category"> in <a href="/vanilla/{cat}">General</a></span></div></div>
<div class="Item-BodyWrap"><div class="Item-Body"><div class="Message userContent">{body_html(t, 'p', q)}</div><div class="UserSignature userContent">{esc(sig(t))}</div><div class="Reactions"><a href="#" class="ReactButton">Like</a></div></div></div></div></div></div>
<div class="CommentsWrap"><span class="BeforeCommentHeading"><h2 class="CommentHeading">Comments</h2></span><div class="DataBox DataBox-Comments"><ul class="MessageList DataList Comments">{comments}</ul></div></div>"""
        add(base + tl(t), f"vanilla/d_{t['id']}.html",
            shell("vanilla_discussion_index", "Discussion Section-Discussion", t["title"], content))


# =========================================================================== custom: old table board (generic)
def oldboard():
    base = "/oldboard/"

    def page_url(p):
        return "forum.php?cat=3" if p == 1 else f"forum.php?cat=3&pg={p}"

    def tl(t):
        return f"topic.php?id={t['id']}"

    def q(author, text):
        return f'<div class="qt"><i>Quote from {esc(author)}:</i><blockquote>{text}</blockquote></div>'

    def pages(p):
        out = ["Pages:"]
        for i in sorted(PAGES):
            out.append(f"<b>{i}</b>" if i == p else f'<a href="{page_url(i)}">{i}</a>')
        if p < LAST_PAGE:
            out.append(f'<a href="{page_url(p + 1)}">Next &raquo;</a>')
        return f'<div class="pages">{" ".join(out)}</div>'

    def shell(title, content):
        return f"""<html><head><title>Old Board - {esc(title)}</title><meta http-equiv="Content-Type" content="text/html; charset=utf-8"></head>
<body bgcolor="#ffffff">
<table width="100%" class="hdr"><tr><td><a href="index.php">Old Board</a> | <a href="search.php">Search</a> | <a href="memberlist.php">Members</a> | <a href="login.php">Login</a></td></tr></table>
<p class="crumbs"><a href="index.php">Index</a> &gt; <a href="forum.php?cat=3">Recovery</a></p>
{content}
<p class="foot"><a href="about.php">About</a> &middot; <a href="contact.php">Contact</a> &middot; Old Board v1.0</p>
</body></html>"""

    for p, ids in PAGES.items():
        rows = "".join(
            f'<tr><td class="t">{"<b>Sticky:</b> " if BY_ID[i]["sticky"] else ""}<a href="{tl(BY_ID[i])}">{esc(BY_ID[i]["title"])}</a></td>'
            f'<td><a href="user.php?u={i}">{BY_ID[i]["author"]}</a></td><td>{len(BY_ID[i]["replies"])}</td><td>Feb 1, 2024</td></tr>'
            for i in ids)
        content = f"""{pages(p)}
<table class="list" cellspacing="1" width="100%"><tr><th>Topic</th><th>Starter</th><th>Replies</th><th>Last post</th></tr>{rows}</table>
{pages(p)}"""
        add(base + page_url(p), f"oldboard/forum_p{p}.html", shell("Recovery", content))

    def row(author, date, body, signature=""):
        s = f'<div class="sig">__________<br>{esc(signature)}</div>' if signature else ""
        return (f'<tr class="p"><td class="who" valign="top" width="150"><a href="user.php?u=5">{author}</a><br><small>Posts: 12</small></td>'
                f'<td class="msg" valign="top"><div class="postdate">Posted: {date}</div><div class="txt">{body}</div>{s}</td></tr>')

    for t in THREADS:
        rows = [row(t["author"], t["date"], body_html(t, "br", q), sig(t))]
        for k in range(len(t["replies"])):
            rows.append(row("Helper_A", "Feb 1, 2024", reply_body(t, k, "br", q)))
        content = f'<h2 class="ttl">{esc(t["title"])}</h2>\n<table class="posts" cellspacing="1" width="100%">{"".join(rows)}</table>'
        add(base + tl(t), f"oldboard/topic_{t['id']}.html", shell(t["title"], content))


# =========================================================================== custom: modern utility-class layout (generic)
def community():
    base = "/community/"

    def page_url(p):
        return "" if p == 1 else f"?page={p}"

    def tl(t):
        return f"posts/{t['id']}-{t['slug']}"

    def q(author, text):
        return f'<figure class="tw-border-l-4 tw-pl-3 cite-block"><figcaption>{esc(author)} wrote</figcaption><blockquote>{text}</blockquote></figure>'

    def shell(title, content):
        return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{esc(title)} · Community</title></head>
<body class="tw-bg-white"><div class="app">
<div class="topbar tw-flex tw-justify-between"><a href="/community/" class="tw-font-bold">Community</a><div class="tw-flex tw-gap-2"><a href="/community/search">Search</a><a href="/community/login">Sign in</a></div></div>
<div class="layout tw-grid tw-grid-cols-3 tw-gap-6">
<div class="main-col tw-col-span-2">{content}</div>
<div class="side-col"><h3>Trending</h3>{''.join(f'<div><a href="/community/{tl(x)}">{esc(x["title"])}</a></div>' for x in THREADS[1:6])}</div>
</div>
<div class="bottom tw-text-xs"><a href="/community/terms">Terms</a> <a href="/community/privacy">Privacy</a></div>
</div></body></html>"""

    removed = {"id": 999, "title": "Removed discussion", "slug": "removed-discussion"}
    for p, ids in PAGES.items():
        items = [BY_ID[i] for i in ids] + ([removed] if p == LAST_PAGE else [])
        rows = "".join(f"""<div class="tw-flex tw-gap-3 tw-py-3 tw-border-b">
<span class="pic tw-rounded-full">O</span><div class="tw-grow"><a class="tw-font-semibold tw-text-lg" href="/community/{tl(t)}">{esc(t['title'])}</a>
<div class="tw-text-sm tw-text-gray-500"><a href="/community/u/{t.get('author', 'x').lower()}">{t.get('author', 'x')}</a> · <span>3 days ago</span> · <span>{len(t.get('replies', []))} replies</span></div></div></div>""" for t in items)
        prev = f'<a href="/community/{page_url(p - 1)}" aria-label="Previous page">&larr;</a>' if p > 1 else "<span></span>"
        nxt = f'<a href="/community/{page_url(p + 1)}" aria-label="Next page">&rarr;</a>' if p < LAST_PAGE else "<span></span>"
        content = f"""<h1 class="tw-text-2xl">Latest discussions</h1><div class="feed-list tw-flex tw-flex-col">{rows}</div>
<div class="tw-flex tw-justify-between tw-mt-4">{prev}<span>Page {p}</span>{nxt}</div>"""
        add(base + page_url(p), f"community/list_p{p}.html", shell("Latest discussions", content))

    def box(author, date, body, signature=""):
        s = f'<div class="tw-text-xs tw-text-gray-400 sig-line">{esc(signature)}</div>' if signature else ""
        return f"""<div class="tw-rounded tw-border tw-p-4 box"><div class="tw-flex tw-items-center tw-gap-2 who"><span class="pic">{author[0]}</span><a href="/community/u/{author.lower()}" class="tw-font-semibold">{author}</a><span class="tw-text-gray-500">{date}</span></div>
<div class="prose tw-mt-2">{body}</div>{s}
<div class="tw-flex tw-gap-3 tw-text-sm tw-mt-2"><button>Like</button><button>Reply</button><a href="#share">Share</a></div></div>"""

    for t in THREADS:
        boxes = [box(t["author"], t["date"], body_html(t, "p", q), sig(t))]
        for k in range(len(t["replies"])):
            boxes.append(box("Helper_A", "Feb 1, 2024", reply_body(t, k, "p", q)))
        content = f"""<div class="thread-head"><h1 class="tw-text-2xl">{esc(t['title'])}</h1><div class="tw-text-sm">in <a href="/community/">Community</a></div></div>
<section class="tw-flex tw-flex-col tw-gap-4">{''.join(boxes)}</section>
<div class="related tw-mt-6"><h3>Related</h3><ul>{''.join(f'<li><a href="/community/{tl(x)}">{esc(x["title"])}</a></li>' for x in THREADS[:3])}</ul></div>"""
        add(base + tl(t), f"community/post_{t['id']}.html", shell(t["title"], content))
    add(base + tl(removed), "community/post_999.html",
        shell("Removed", '<div class="tw-p-4"><a href="/community/">Back to discussions</a></div>'))


# =========================================================================== custom: JavaScript-rendered single page app
def spa():
    base = "/spa/"

    def page_url(p):
        return f"board?p={p}"

    def tl(t):
        return f"thread/{t['id']}"

    def shell(title, data, renderer):
        return f"""<!doctype html><html><head><meta charset="utf-8"><title>{esc(title)}</title></head>
<body><div id="root">Loading…</div>
<script>
const DATA = {json.dumps(data)};
{renderer}
</script></body></html>"""

    list_renderer = """
function esc(s){const d=document.createElement('div');d.textContent=s;return d.innerHTML;}
setTimeout(function(){
  let h = '<nav class="menu"><a href="/spa/board?p=1">Board</a> <a href="/spa/login">Login</a></nav>';
  h += '<h1>Board</h1><ol class="items">';
  for (const t of DATA.threads) {
    h += '<li class="item"><a class="link" href="/spa/thread/' + t.id + '">' + esc(t.title) + '</a>'
       + '<p class="meta">by <a href="/spa/user/' + t.author + '">' + t.author + '</a> · ' + t.replies + ' replies</p></li>';
  }
  h += '</ol><div class="pager">';
  if (DATA.prev) h += '<a class="prev" href="/spa/board?p=' + DATA.prev + '">‹ Previous</a> ';
  if (DATA.next) h += '<a class="next" href="/spa/board?p=' + DATA.next + '">Next ›</a>';
  h += '</div>';
  document.getElementById('root').innerHTML = h;
}, 50);
"""
    for p, ids in PAGES.items():
        data = {"threads": [{"id": BY_ID[i]["id"], "title": BY_ID[i]["title"], "author": BY_ID[i]["author"],
                             "replies": len(BY_ID[i]["replies"])} for i in ids],
                "prev": p - 1 if p > 1 else None, "next": p + 1 if p < LAST_PAGE else None}
        add(base + page_url(p), f"spa/board_p{p}.html", shell("Board", data, list_renderer))

    thread_renderer = """
setTimeout(function(){
  let h = '<nav class="menu"><a href="/spa/board?p=1">Board</a></nav><h1>' + DATA.title + '</h1><div class="thread">';
  for (const p of DATA.posts) {
    h += '<div class="entry" id="post-' + p.n + '"><div class="byline"><span class="author">' + p.author + '</span> <span class="date">' + p.date + '</span></div>'
       + '<div class="entry-body">' + p.body + '</div></div>';
  }
  h += '</div>';
  document.getElementById('root').innerHTML = h;
}, 50);
"""

    def q(author, text):
        return f'<blockquote class="quote"><b>{esc(author)}:</b> {text}</blockquote>'

    for t in THREADS:
        posts = [{"n": 1, "author": t["author"], "date": t["date"], "body": body_html(t, "p", q)}]
        for k in range(len(t["replies"])):
            posts.append({"n": k + 2, "author": "Helper_A", "date": "Feb 1, 2024", "body": reply_body(t, k, "p", q)})
        add(base + tl(t), f"spa/thread_{t['id']}.html",
            shell(t["title"], {"title": esc(t["title"]), "posts": posts}, thread_renderer))


# =========================================================================== misc pages
def misc():
    add("/blocked/forum", "misc/blocked.html", """<!DOCTYPE html><html><head><title>Just a moment...</title></head>
<body><div class="main-wrapper"><div class="main-content"><h1>Checking your browser before accessing the site.</h1>
<div id="challenge-stage"><div class="cf-challenge"></div></div><script src="/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1"></script></div></div></body></html>""",
        status=403)
    add("/robots.txt", "misc/robots.txt", "User-agent: *\nDisallow: /private/\n", content_type="text/plain")
    add("/private/forum", "misc/private.html", "<html><body><a href='/private/t/1'>Secret thread one</a></body></html>")
    add("/plain/about", "misc/about.html", """<html><head><title>About us</title></head><body><nav><a href="/">Home</a></nav>
<h1>About us</h1><p>We are a small company.</p></body></html>""")


def ambiguous():
    """A listing whose only pagination is an unlabeled 'More topics' link: the job must ask the user."""
    def page(ids, more):
        rows = "".join(f'<tr><td class="t"><a href="topic.php?id={i}">{esc(BY_ID[i]["title"])}</a></td><td>by {BY_ID[i]["author"]}</td>'
                       f'<td>{len(BY_ID[i]["replies"])} replies</td></tr>' for i in ids)
        more_html = f'<p><a href="{more}">More topics</a></p>' if more else ""
        return (f"<html><head><title>Ambiguous board</title></head><body><h1>Board</h1>"
                f'<table class="list">{rows}</table>{more_html}</body></html>')
    add("/ambig/list", "ambig/list1.html", page([2, 3, 5], "list?offset=20"))
    add("/ambig/list?offset=20", "ambig/list2.html", page([6, 8, 9], None))
    for i in (2, 3, 5, 6, 8, 9):
        ROUTES[f"/ambig/topic.php?id={i}"] = {"file": f"oldboard/topic_{i}.html"}


def build() -> None:
    if ROOT.exists():
        shutil.rmtree(ROOT)
    ROOT.mkdir(parents=True)
    ROUTES.clear()
    for fn in (xf2, xf1, phpbb, vbulletin, discourse, nodebb, flarum, vanilla, oldboard, community, spa, misc, ambiguous):
        fn()
    (ROOT / "routes.json").write_text(json.dumps(ROUTES, indent=1, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    build()
    print(f"wrote {len(ROUTES)} routes to {ROOT}")
