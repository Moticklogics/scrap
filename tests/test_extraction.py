import pytest

from conftest import FAKE_BASE, PLATFORM_OF, STATIC_SITES, fixture_html, routes_for, thread_id_of
from forum_data import BY_ID, FORBIDDEN_MARKERS, expected_text
from forum_extractor.config import SiteProfile
from forum_extractor.detection import analyze_html
from forum_extractor.extraction import extract_original_post
from forum_extractor.platforms import get_platform
from forum_extractor.textutil import soup_from_html

CASES = [(s, r) for s in STATIC_SITES for r in routes_for(s, "thread") if thread_id_of(r) in BY_ID]


@pytest.mark.parametrize("site,route", CASES)
def test_first_post_only_exact_text(site, route):
    """Original post only: no replies, quotes, usernames, dates or signatures; structure preserved."""
    t = BY_ID[thread_id_of(route)]
    spec = get_platform(PLATFORM_OF[site])
    res = extract_original_post(soup_from_html(fixture_html(route)), FAKE_BASE + route, spec, None, 0.6)
    if site == "community" and not t["replies"]:
        # single-post thread on an unknown layout: must be flagged, not silently accepted
        assert not res.ok and "Configure selectors" in res.error
        return
    assert res.ok, res.error
    assert res.text == expected_text(t)
    for marker in FORBIDDEN_MARKERS:
        assert marker not in res.text
    assert t["date"] not in res.text


@pytest.mark.parametrize("site,route", CASES)
def test_generic_engine_without_platform_knowledge(site, route):
    """Even with platform detection disabled the generic DOM analysis finds the original post."""
    t = BY_ID[thread_id_of(route)]
    res = extract_original_post(soup_from_html(fixture_html(route)), FAKE_BASE + route, None, None, 0.6)
    if site == "community" and not t["replies"]:
        assert not res.ok
        return
    assert res.ok, res.error
    assert res.text == expected_text(t)


def test_learned_profile_handles_single_post_threads():
    first = extract_original_post(soup_from_html(fixture_html("/community/posts/2-day-1-starting-my-journey")),
                                  FAKE_BASE + "/community/posts/2", None, None, 0.6)
    assert first.ok and first.method == "structural"
    prof = SiteProfile(domain="forum.test", post_container_selector=first.container_selector,
                       post_body_selector=first.body_selector, exclude_selectors=first.exclude_selectors)
    single = extract_original_post(soup_from_html(fixture_html("/community/posts/4-just-checking-in")),
                                   FAKE_BASE + "/community/posts/4", None, prof, 0.6)
    assert single.ok and single.text == expected_text(BY_ID[4])


def test_low_confidence_page_is_reported_not_guessed():
    res = extract_original_post(soup_from_html(fixture_html("/community/posts/999-removed-discussion")),
                                FAKE_BASE + "/community/posts/999", None, None, 0.6)
    assert not res.ok and res.text == "" and "manually" in res.error


def test_listing_page_is_not_mistaken_for_a_thread():
    route = "/xenforo2/index.php?forums/recovery.2/"
    res = extract_original_post(soup_from_html(fixture_html(route)), FAKE_BASE + route, get_platform("xenforo2"))
    assert not res.ok and "listing" in res.error


def test_later_thread_page_is_rejected():
    html = fixture_html("/xenforo2/index.php?threads/day-1-starting-my-journey.2/")
    res = extract_original_post(soup_from_html(html), FAKE_BASE + "/xenforo2/index.php?threads/x.2/page-3",
                                get_platform("xenforo2"))
    assert not res.ok and "page 3" in res.error


def test_wrong_user_selector_is_caught_by_validation():
    """A user selector that points at all posts' wrapper must fail validation (contains replies)."""
    prof = SiteProfile(domain="forum.test", source="user", post_container_selector="div.block-body",
                       post_body_selector="")
    html = fixture_html("/xenforo2/index.php?threads/day-1-starting-my-journey.2/")
    res = extract_original_post(soup_from_html(html), FAKE_BASE + "/x", None, prof, 0.6, manual_only=True)
    assert not res.ok


def test_quotes_can_be_kept():
    html = fixture_html("/phpbb/viewtopic.php?f=2&t=6")
    res = extract_original_post(soup_from_html(html), FAKE_BASE + "/phpbb/viewtopic.php?f=2&t=6",
                                get_platform("phpbb"), exclude_quotes=False)
    assert res.ok and "QUOTED-TEXT-MARKER" in res.text


class FakeAI:
    """Stands in for the local LLM: it may only choose an index."""
    def __init__(self):
        self.seen = None

    def choose(self, candidates):
        self.seen = [c.text for c in candidates]
        return 0, "first candidate is the opening post"


def test_ai_classifier_only_chooses_existing_candidates():
    ai = FakeAI()
    html = fixture_html("/community/posts/2-day-1-starting-my-journey")
    res = extract_original_post(soup_from_html(html), FAKE_BASE + "/community/posts/2", None, None,
                                threshold=0.99, classifier=ai)
    assert ai.seen, "classifier consulted for low-confidence page"
    assert res.text in ai.seen  # text comes from the DOM candidate, never generated
    assert res.method.startswith("ai+") and res.ai_reason


def test_page_analysis_reports_thread_type_for_thread_urls():
    route = "/vanilla/discussion/2/day-1-starting-my-journey"
    a = analyze_html(fixture_html(route), FAKE_BASE + route)
    assert a.page_type == "thread"
