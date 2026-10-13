import pytest

from conftest import FAKE_BASE, LISTING_START, PLATFORM_OF, STATIC_SITES, fixture_html, routes_for
from forum_data import BY_ID, PAGES
from forum_extractor.detection import analyze_html


@pytest.mark.parametrize("site", STATIC_SITES)
def test_platform_detection_and_page_types(site):
    for route in routes_for(site, "listing"):
        html = fixture_html(route)
        a = analyze_html(html, FAKE_BASE + route, start_url=FAKE_BASE + LISTING_START[site])
        if not a.threads.links:
            continue  # e.g. the empty Discourse page past the end
        assert a.page_type == "listing", route
        assert (a.platform.name if a.platform else None) == PLATFORM_OF[site], route
    for route in routes_for(site, "thread")[:3]:
        a = analyze_html(fixture_html(route), FAKE_BASE + route)
        assert (a.platform.name if a.platform else None) == PLATFORM_OF[site]


@pytest.mark.parametrize("site", STATIC_SITES)
def test_thread_discovery_titles_and_order(site):
    for route in routes_for(site, "listing"):
        a = analyze_html(fixture_html(route), FAKE_BASE + route, start_url=FAKE_BASE + LISTING_START[site])
        if not a.threads.links:
            continue
        page = a.page_number
        expected = [BY_ID[i]["title"] for i in PAGES[page]]
        got = [l.title for l in a.threads.links]
        assert got[: len(expected)] == expected, (route, got)
        assert all(l.url.startswith(FAKE_BASE) for l in a.threads.links)


@pytest.mark.parametrize("site", STATIC_SITES)
def test_pagination_discovery(site):
    for route in routes_for(site, "listing"):
        a = analyze_html(fixture_html(route), FAKE_BASE + route, start_url=FAKE_BASE + LISTING_START[site])
        if not a.threads.links:
            continue
        p = a.pagination
        if a.page_number < max(PAGES):
            assert p.next_url, route
            nxt = analyze_html(fixture_html(p.next_url[len(FAKE_BASE):]), p.next_url,
                               start_url=FAKE_BASE + LISTING_START[site])
            assert nxt.page_number == a.page_number + 1, (route, p.next_url)
        elif not p.inferred:  # Discourse infinite scroll keeps probing ?page=N; verified by the engine
            assert p.next_url is None and p.is_last, route
        assert not p.needs_confirmation


def test_generic_detection_without_platform_markup():
    a = analyze_html(fixture_html("/oldboard/forum.php?cat=3"), FAKE_BASE + "/oldboard/forum.php?cat=3")
    assert a.platform is None and a.page_type == "listing"
    assert a.threads.method == "generic" and a.threads.confidence >= 0.6
    assert a.threads.selector  # a reusable selector was generated


def test_loop_protection_and_ambiguous_pagination():
    url = FAKE_BASE + "/xenforo2/index.php?forums/recovery.2/"
    visited = {FAKE_BASE + "/xenforo2/index.php?forums/recovery.2/page-2"}
    a = analyze_html(fixture_html("/xenforo2/index.php?forums/recovery.2/"), url, visited=visited)
    assert a.pagination.next_url is None  # already visited page is never proposed again
    b = analyze_html(fixture_html("/ambig/list"), FAKE_BASE + "/ambig/list")
    assert b.pagination.needs_confirmation
    assert any("offset=20" in c.url for c in b.pagination.candidates)


def test_blocking_detection():
    a = analyze_html(fixture_html("/blocked/forum"), FAKE_BASE + "/blocked/forum", status=403)
    assert a.page_type == "blocked"


def test_guest_reply_notice_and_captcha_widget_are_not_a_login_wall():
    """XenForo shows 'You must log in or register to reply here.' (and may load reCAPTCHA) on every thread."""
    from forum_extractor.detection import detect_blocking
    from forum_extractor.textutil import soup_from_html
    route = "/xenforo2/index.php?threads/just-checking-in.4/"  # short single-post thread
    html = fixture_html(route)
    assert "You must log in or register to reply here." in html
    a = analyze_html(html, FAKE_BASE + route)
    assert a.page_type == "thread"
    wall = fixture_html("/xenforo2/index.php?threads/members-only.99/")
    assert detect_blocking(wall, soup_from_html(wall), 403)
    assert analyze_html(wall, FAKE_BASE + "/x", status=403).page_type == "blocked"
