"""Shared content used to build every fixture forum, plus the exact text the extractor must produce."""
from __future__ import annotations

import html
import re

# Block types:
#   ("p", html)                 paragraph (may contain inline tags)
#   ("lines", [html, html])     one paragraph with single line breaks
#   ("ul", [html, ...]) / ("ol", [html, ...])
#   ("quote", author, text)     quoted text from someone else (must be excluded)
#   ("code", text)              preformatted text


def _t(i: int, title: str, blocks: list, replies: list[str], sticky: bool = False) -> dict:
    return {"id": i, "title": title, "blocks": blocks, "replies": replies, "sticky": sticky,
            "author": f"OPAuthor_{i}", "date": f"Jan {i}, 2024", "slug": slugify(title)}


def slugify(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")[:40] or "thread"


THREADS = [
    _t(1, "Forum rules - please read before posting", [
        ("p", "Welcome to the recovery forum. Please follow these rules:"),
        ("ol", ["Be respectful to other members.", "No explicit content.", "Stay on topic."]),
        ("p", "Moderators may remove posts that break the rules."),
    ], ["REPLY-MARKER-1a Thanks for the rules."], sticky=True),
    _t(2, "Day 1: starting my journey", [
        ("p", "Today is day one. I’m nervous but “determined” to change \U0001F642"),
        ("p", "My plan is to exercise every morning and to <b>journal</b> every evening."),
    ], ["QUOTE-OP REPLY-MARKER-2a Stay strong, you can do it!", "REPLY-MARKER-2b Good luck with your plan."]),
    _t(3, "Relapsed after 30 days - what now?", [
        ("p", "I relapsed last night after a 30 day streak."),
        ("p", "Things that helped me before:"),
        ("ul", ["Cold showers", "Calling a friend", "Going for a <i>long</i> walk"]),
        ("p", "Any advice is welcome."),
    ], ["REPLY-MARKER-3a Don't give up, reset and keep going."]),
    _t(4, "Just checking in", [
        ("p", "Just checking in to say week two is going well."),
    ], []),
    _t(5, "Café thoughts: naïve résumé of my week", [
        ("p", "Je suis allé au café tous les jours. ¡Qué bien! 你好，世界。"),
        ("p", "Спасибо за поддержку — «удачи»."),
    ], ["REPLY-MARKER-5a Merci!"]),
    _t(6, "Responding to an old thread", [
        ("quote", "Old_Member", "QUOTED-TEXT-MARKER This is what someone else wrote years ago."),
        ("p", "I agree with the quote above, and here is my own experience."),
        ("p", "It took me three tries to get past the first month."),
    ], ["REPLY-MARKER-6a Interesting perspective."]),
    _t(7, "My tracking script", [
        ("p", "I wrote a tiny script to count days:"),
        ("code", "days = (today - start).days\nif days > 30:\n    print(\"milestone <30> reached\")"),
        ("p", "Feel free to use it."),
    ], ["REPLY-MARKER-7a Nice script!"]),
    _t(8, "Links and resources that helped", [
        ("p", "I found <a href=\"https://example.org/guide\">this guide</a> very useful, especially the "
              "<em>second chapter</em>."),
        ("p", "Also the book list on the wiki &amp; the FAQ."),
    ], ["REPLY-MARKER-8a Thanks for sharing."]),
    _t(9, "Poem for anyone struggling", [
        ("lines", ["Roses fall but roots remain,", "storms will pass and light again,", "one more day, then one more day."]),
        ("p", "Stay strong everyone."),
    ], ["REPLY-MARKER-9a Beautiful."]),
    _t(10, "Long post about my first 90 days", [
        ("p", "The first thirty days were the hardest. " * 6),
        ("p", "The second month I noticed better sleep and more energy. " * 5),
        ("p", "By day ninety I had new habits that felt natural."),
    ], ["REPLY-MARKER-10a Congratulations!", "REPLY-MARKER-10b Inspiring."]),
]
BY_ID = {t["id"]: t for t in THREADS}

# Listing pages (1-based) -> thread ids. The sticky thread (1) appears on every page.
PAGES = {1: [1, 2, 3, 4], 2: [1, 5, 6, 7], 3: [1, 8, 9, 10]}


def plain(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def expected_text(thread: dict) -> str:
    """Exact text the extractor must output for the original post (quotes removed)."""
    out = []
    for b in thread["blocks"]:
        kind = b[0]
        if kind == "p":
            out.append(re.sub(r"\s+", " ", plain(b[1])).strip())
        elif kind == "lines":
            out.append("\n".join(plain(x) for x in b[1]))
        elif kind == "ul":
            out.append("\n".join("- " + plain(x) for x in b[1]))
        elif kind == "ol":
            out.append("\n".join(f"{i}. " + plain(x) for i, x in enumerate(b[1], 1)))
        elif kind == "code":
            out.append(b[1])
        elif kind == "quote":
            continue
    return "\n\n".join(out)


def reply_text(thread: dict, k: int) -> str:
    r = thread["replies"][k]
    return r.replace("QUOTE-OP ", "")


def reply_quotes_op(thread: dict, k: int) -> bool:
    return thread["replies"][k].startswith("QUOTE-OP")


FORBIDDEN_MARKERS = ["REPLY-MARKER", "SIGNATURE-MARKER", "QUOTED-TEXT-MARKER", "Helper_A said", "OPAuthor_"]
