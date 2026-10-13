from bs4 import BeautifulSoup

from forum_extractor.textutil import html_to_text
from forum_extractor.urls import canonical_thread_url, in_scope, listing_base, normalize_url, thread_key


def test_thread_url_canonicalisation():
    assert canonical_thread_url("https://f.com/index.php?threads/a-b.12/latest") == "https://f.com/index.php?threads/a-b.12/"
    assert canonical_thread_url("https://f.com/index.php?threads/a-b.12/page-4") == "https://f.com/index.php?threads/a-b.12/"
    assert canonical_thread_url("https://x.org/viewtopic.php?f=2&t=9&sid=0123456789abcdef0123456789abcdef&start=20") \
        == "https://x.org/viewtopic.php?f=2&t=9"
    assert canonical_thread_url("https://x.org/t/slug/55/7") == "https://x.org/t/slug/55"
    assert canonical_thread_url("https://x.org/discussion/12/slug/p3") == "https://x.org/discussion/12/slug"
    assert canonical_thread_url("https://x.org/showthread.php?t=5&goto=newpost") == "https://x.org/showthread.php?t=5"


def test_thread_key_ignores_slug_and_variants():
    a = thread_key("https://www.f.com/index.php?threads/old-title.12/")
    b = thread_key("https://f.com/index.php?threads/new-title.12/unread")
    assert a == b


def test_scope_and_listing_base():
    assert in_scope("https://forum.example.com/x", "https://www.forum.example.com/y")
    assert not in_scope("https://ads.other.com/x", "https://forum.example.com/")
    assert in_scope("https://cdn.other.com/x", "https://forum.example.com/", ["other.com"])
    assert listing_base("https://f.com/index.php?forums/a.2/page-3") == listing_base("https://f.com/index.php?forums/a.2/")
    assert listing_base("https://f.com/viewforum.php?f=2&start=40") == "https://f.com/viewforum.php?f=2"
    assert normalize_url("HTTPS://F.com:443/a#frag") == "https://f.com/a"


def test_html_to_text_preserves_structure():
    html = """<div><p>First paragraph with <b>bold</b>
    text.</p>line one<br>line two<ul><li>apple</li><li>pear<ul><li>nested</li></ul></li></ul>
    <ol><li>one</li><li>two</li></ol><pre>  code   keeps
  spacing</pre><blockquote>quoted</blockquote><script>evil()</script>
    <img class="smilie" alt="🙂"><span style="display:none">hidden</span></div>"""
    el = BeautifulSoup(html, "lxml").div
    text = html_to_text(el, ["blockquote"])
    assert text == ("First paragraph with bold text.\n\nline one\nline two\n\n- apple\n- pear\n  - nested\n\n"
                    "1. one\n2. two\n\n  code   keeps\n  spacing\n\n🙂")
    assert "quoted" not in text and "evil" not in text and "hidden" not in text
