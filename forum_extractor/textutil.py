"""Convert a post-body DOM element to clean plain text.

Paragraph breaks, line breaks, lists and preformatted blocks are preserved; wording is kept verbatim.
"""
from __future__ import annotations

import copy
import re
from typing import Callable

from bs4 import BeautifulSoup, Comment, NavigableString, Tag
from bs4.element import CData, Declaration, Doctype, ProcessingInstruction

from .domutil import classes, safe_select

BLOCK_TAGS = {
    "p", "div", "section", "article", "blockquote", "ul", "ol", "dl", "dt", "dd", "table", "thead", "tbody",
    "tfoot", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "figure", "figcaption", "header", "footer", "aside",
    "main", "details", "summary", "address", "center", "fieldset", "legend", "caption",
}
REMOVE_SELECTOR = (
    "script, style, noscript, template, button, svg, iframe, form, input, select, textarea, object, embed, "
    "canvas, [hidden], [aria-hidden='true']"
)
_INDENT = "\u0001"
_PRE = "\u0002"


def _is_hidden(el: Tag) -> bool:
    style = (el.get("style") or "").replace(" ", "").lower()
    return "display:none" in style or "visibility:hidden" in style


def html_to_text(
    element: Tag,
    exclude_selectors: list[str] | tuple[str, ...] = (),
    exclude_pred: Callable[[Tag], bool] | None = None,
) -> str:
    """Return the visible text of `element` with structure preserved."""
    # Work on a private copy so the analysed document is never modified.
    el = copy.copy(element)
    if exclude_pred is not None:
        # The predicate is evaluated against the original tree, then mapped to the copy by position.
        orig_tags = [element, *element.find_all(True)]
        copy_tags = [el, *el.find_all(True)]
        doomed = [c for o, c in zip(orig_tags, copy_tags) if o is not element and exclude_pred(o)]
        for c in doomed:
            if c.parent is not None:
                c.decompose()
    for sel in exclude_selectors:
        for x in safe_select(el, sel):
            if x is not el:
                x.decompose()
    for x in safe_select(el, REMOVE_SELECTOR):
        x.decompose()
    for x in el.find_all(True):
        if x.parent is not None and _is_hidden(x) and not any("spoiler" in c.lower() for c in classes(x)):
            x.decompose()

    parts: list[str] = []
    pres: list[str] = []
    _walk(el, parts, pres, list_stack=[])
    text = "".join(parts)
    lines = []
    for line in text.split("\n"):
        line = re.sub(r"[ \t ]+", " ", line).strip()
        line = line.replace(_INDENT, "  ")
        lines.append(line)
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    for i, raw in enumerate(pres):
        text = text.replace(f"{_PRE}{i}{_PRE}", raw)
    return text


def _walk(node, parts: list[str], pres: list[str], list_stack: list[list]) -> None:
    for child in list(node.children):
        if isinstance(child, (Comment, CData, ProcessingInstruction, Declaration, Doctype)):
            continue
        if isinstance(child, NavigableString):
            s = str(child)
            parts.append(re.sub(r"\s+", " ", s))
            continue
        if not isinstance(child, Tag):
            continue
        name = child.name
        if name == "br":
            parts.append("\n")
        elif name == "hr":
            parts.append("\n\n")
        elif name == "img":
            alt = (child.get("alt") or "").strip()
            cls = " ".join(classes(child)).lower()
            if alt and ("smil" in cls or "emoji" in cls or "emoticon" in cls):
                parts.append(alt)
        elif name == "pre":
            raw = child.get_text()
            raw = raw.strip("\n")
            pres.append(raw)
            parts.append(f"\n\n{_PRE}{len(pres) - 1}{_PRE}\n\n")
        elif name in ("ul", "ol"):
            list_stack.append([name, 0])
            parts.append("" if len(list_stack) > 1 else "\n\n")
            _walk(child, parts, pres, list_stack)
            list_stack.pop()
            parts.append("" if list_stack else "\n\n")
        elif name == "li":
            depth = max(0, len(list_stack) - 1)
            if list_stack and list_stack[-1][0] == "ol":
                list_stack[-1][1] += 1
                marker = f"{list_stack[-1][1]}. "
            else:
                marker = "- "
            parts.append("\n" + _INDENT * depth + marker)
            _walk(child, parts, pres, list_stack)
        elif name in ("td", "th"):
            _walk(child, parts, pres, list_stack)
            parts.append(" ")
        elif name in BLOCK_TAGS:
            if list_stack and name in ("p", "div"):
                # paragraphs inside list items should not split the item
                _walk(child, parts, pres, list_stack)
                continue
            parts.append("\n\n")
            _walk(child, parts, pres, list_stack)
            parts.append("\n\n")
        else:
            _walk(child, parts, pres, list_stack)


def soup_from_html(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")
