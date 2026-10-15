"""End-to-end tests: real Chromium (Playwright) against the local fixture forums."""
import asyncio
import re
from pathlib import Path

import pytest

from conftest import LISTING_START
from forum_data import BY_ID, FORBIDDEN_MARKERS, PAGES, expected_text
from forum_extractor.config import JobConfig, SiteProfile
from forum_extractor.engine import JobManager
from forum_extractor.profiles import ProfileStore
from forum_extractor.storage import Database

pytestmark = pytest.mark.browser


def parse_records(out_dir: Path) -> list[dict]:
    recs = []
    for f in sorted(out_dir.glob("*.txt")):
        text = f.read_text(encoding="utf-8")
        for m in re.finditer(r"={80}\nLISTING PAGE: (.*?)\nTHREAD TITLE: (.*?)\nTHREAD URL: (.*?)\nSTATUS: (.*?)\n"
                             r"ORIGINAL POST:\n(.*?)\n={80}\n", text, re.S):
            recs.append({"file": f.name, "page": m.group(1), "title": m.group(2), "url": m.group(3),
                         "status": m.group(4), "text": m.group(5)})
    return recs


class Env:
    def __init__(self, tmp: Path, server):
        self.tmp = tmp
        self.server = server
        self.db = Database(tmp / "state.sqlite3")
        self.store = ProfileStore(tmp / "profiles")
        self.out = tmp / "out"

    def cfg(self, path: str, **kw) -> JobConfig:
        base = dict(start_url=self.server.base + path, output_dir=str(self.out), request_delay=0,
                    max_concurrency=2, pages_per_file=2, page_timeout=20, max_retries=2)
        base.update(kw)
        return JobConfig.from_dict(base)

    async def run(self, cfg: JobConfig, until=None, timeout=120) -> tuple[JobManager, str]:
        m = JobManager(self.db, self.store)
        job = m.create(cfg)
        m.start(job)
        await self.wait(m, job, until, timeout)
        return m, job

    async def wait(self, m, job, until=None, timeout=120):
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        while m.is_running(job):
            if until and until(m, job):
                return
            if loop.time() > end:
                raise TimeoutError(m.status(job))
            await asyncio.sleep(0.1)
        await m.tasks[job]


@pytest.fixture
def env(tmp_path, fresh_server):
    return Env(tmp_path, fresh_server)


@pytest.mark.parametrize("site", list(LISTING_START))
def test_full_job_per_layout(env, site):
    async def go():
        return await env.run(env.cfg(LISTING_START[site]))
    m, job = asyncio.run(go())
    st = m.status(job)
    assert st["status"] == "completed", st
    recs = parse_records(env.out)
    assert sorted({r["file"] for r in recs}) == ["Forum_Pages_0001-0002.txt", "Forum_Pages_0003-0003.txt"]
    if site == "reddit":
        # one extra title-only question on page 2; ads and image posts are never collected
        from forum_extractor.platforms import EMPTY_BODY_TEXT
        extra = [r for r in recs if r["title"] == "Is cold turkey better than tapering?"]
        assert len(extra) == 1 and extra[0]["text"] == EMPTY_BODY_TEXT and extra[0]["page"] == "2"
        assert not any("premium habit app" in r["title"] or "progress chart" in r["title"] for r in recs)
        recs = [r for r in recs if r not in extra]
    assert len(recs) == 10, [r["title"] for r in recs]
    assert st["threads_duplicate"] == 2  # sticky thread repeated on pages 2 and 3
    by_title = {t["title"]: t for t in BY_ID.values()}
    for r in recs:
        t = by_title[r["title"]]
        page_of = min(p for p, ids in PAGES.items() if t["id"] in ids)
        assert r["page"] == str(page_of)
        assert r["text"] == expected_text(t), (site, r["title"])
        assert not any(mk in r["text"] for mk in FORBIDDEN_MARKERS)
    if site == "community":
        assert st["threads_failed"] == 1  # the removed thread is recorded as a failure, not written
    else:
        assert st["threads_failed"] == 0


def test_start_on_page_two_default_rotation(env):
    async def go():
        return await env.run(env.cfg("/xenforo2/index.php?forums/recovery.2/page-2", pages_per_file=1100))
    m, job = asyncio.run(go())
    assert m.status(job)["status"] == "completed"
    assert [p.name for p in env.out.iterdir()] == ["Forum_Pages_0002-0003.txt"]


def test_flaky_thread_is_retried(env):
    async def go():
        return await env.run(env.cfg("/xenforo2/index.php?forums/recovery.2/page-2", max_retries=2))
    m, job = asyncio.run(go())
    st = m.status(job)
    assert st["status"] == "completed" and st["threads_failed"] == 0
    route = "/xenforo2/index.php?threads/responding-to-an-old-thread.6/"
    assert env.server.hits[route] == 2  # 503 once, then success


def test_stop_and_resume_without_duplicates(env):
    async def go():
        cfg = env.cfg("/phpbb/viewforum.php?f=2", max_concurrency=1)
        m = JobManager(env.db, env.store)
        job = m.create(cfg)
        m.start(job)
        await env.wait(m, job, until=lambda m, j: (m.db.get_page(j, 0) or {}).get("status") == "done")
        m.runner(job).stop()
        await m.tasks[job]
        st1 = m.status(job)
        m.start(job)  # resume
        await env.wait(m, job)
        return st1, m.status(job)
    st1, st2 = asyncio.run(go())
    assert st1["status"] == "stopped"
    assert st2["status"] == "completed"
    recs = parse_records(env.out)
    urls = [r["url"] for r in recs]
    assert len(urls) == len(set(urls)) == 10


def test_crash_mid_page_then_resume(env):
    async def go():
        cfg = env.cfg("/vanilla/categories/general", max_concurrency=1)
        m = JobManager(env.db, env.store)
        job = m.create(cfg)
        m.start(job)
        await env.wait(m, job, until=lambda m, j: m.db.thread_counts(j).get("done", 0) >= 2)
        m.tasks[job].cancel()  # simulated crash / kill
        try:
            await m.tasks[job]
        except asyncio.CancelledError:
            pass
        env.db.update_job(job, status="running")
        env.db.mark_interrupted_jobs()  # what the app does on next start
        assert env.db.get_job(job)["status"] == "interrupted"
        m2 = JobManager(env.db, env.store)
        m2.start(job)
        await env.wait(m2, job)
        return m2.status(job)
    st = asyncio.run(go())
    assert st["status"] == "completed"
    urls = [r["url"] for r in parse_records(env.out)]
    assert len(urls) == len(set(urls)) == 10


def test_duplicates_kept_when_disabled(env):
    async def go():
        return await env.run(env.cfg("/nodebb/category/2/general-discussion", skip_duplicates=False))
    m, job = asyncio.run(go())
    recs = parse_records(env.out)
    assert len(recs) == 12 and m.status(job)["threads_duplicate"] == 0


def test_max_pages(env):
    async def go():
        return await env.run(env.cfg("/oldboard/forum.php?cat=3", max_pages=2, pages_per_file=1100))
    m, job = asyncio.run(go())
    st = m.status(job)
    assert st["status"] == "completed" and st["listing_pages_done"] == 2
    assert [p.name for p in env.out.iterdir()] == ["Forum_Pages_0001-0002.txt"]


def test_low_confidence_then_retry_failed(env):
    async def go():
        m, job = await env.run(env.cfg("/oldboard/forum.php?cat=3", confidence_threshold=0.99))
        st1 = m.status(job)
        st1["records"] = parse_records(env.out)
        cfg = env.db.get_job(job)["config"]
        cfg["confidence_threshold"] = 0.6
        env.db.update_job(job, config=cfg)
        m.start(job, "retry_failed")
        await env.wait(m, job)
        return st1, m.status(job)
    st1, st2 = asyncio.run(go())
    assert st1["threads_failed"] == 10 and st1["threads_completed"] == 0 and st1["threads_duplicate"] == 2
    assert st1["records"] == []  # nothing silently written below the threshold
    assert st2["threads_failed"] == 0 and st2["threads_completed"] == 10
    recs = parse_records(env.out)
    assert len(recs) == 10
    assert {r["file"] for r in recs} == {"Forum_Pages_0001-0002.txt", "Forum_Pages_0003-0003.txt"}


def test_blocked_site_is_not_bypassed(env):
    async def go():
        return await env.run(env.cfg("/blocked/forum"))
    m, job = asyncio.run(go())
    st = m.status(job)
    assert st["status"] == "blocked" and "does not bypass" in st["message"]


def test_robots_txt_is_respected(env):
    async def go():
        return await env.run(env.cfg("/private/forum"))
    m, job = asyncio.run(go())
    assert m.status(job)["status"] == "blocked"


def test_unknown_pagination_asks_user(env):
    async def go():
        m = JobManager(env.db, env.store)
        job = m.create(env.cfg("/ambig/list"))
        m.start(job)
        await env.wait(m, job, until=lambda m, j: m.runner(j).state == "awaiting_confirmation")
        pending = m.runner(job).pending_confirmation
        assert any("offset=20" in c["url"] for c in pending["candidates"])
        m.runner(job).confirm_next(env.server.base + "/ambig/list?offset=20")
        await env.wait(m, job)
        return m.status(job)
    st = asyncio.run(go())
    assert st["status"] == "completed" and st["threads_completed"] == 6


def test_unknown_pagination_stop_mode(env):
    async def go():
        return await env.run(env.cfg("/ambig/list", on_unknown_pagination="stop"))
    m, job = asyncio.run(go())
    st = m.status(job)
    assert st["status"] == "completed" and st["threads_completed"] == 3


def test_single_thread_url(env):
    async def go():
        return await env.run(env.cfg("/discourse/t/my-tracking-script/7"))
    m, job = asyncio.run(go())
    recs = parse_records(env.out)
    assert len(recs) == 1 and recs[0]["page"] == "N/A" and recs[0]["text"] == expected_text(BY_ID[7])


def test_manual_mode_uses_saved_profile(env):
    env.store.save(SiteProfile(domain="127.0.0.1", source="user", thread_link_selector="td.t a",
                               next_page_selector="div.pages a:-soup-contains('Next')",
                               post_container_selector="table.posts tr.p", post_body_selector="div.txt",
                               exclude_selectors=["div.qt"]))

    async def go():
        return await env.run(env.cfg("/oldboard/forum.php?cat=3", mode="manual"))
    m, job = asyncio.run(go())
    st = m.status(job)
    assert st["status"] == "completed" and st["threads_completed"] == 10
    for r in parse_records(env.out):
        t = next(t for t in BY_ID.values() if t["title"] == r["title"])
        assert r["text"] == expected_text(t)


def test_manual_mode_without_profile_fails_clearly(env):
    async def go():
        return await env.run(env.cfg("/oldboard/forum.php?cat=3", mode="manual"))
    m, job = asyncio.run(go())
    st = m.status(job)
    assert st["status"] == "error" and "profile" in st["message"]


def test_login_walled_thread_is_skipped_not_fatal(env):
    from forum_extractor.service import test_thread as probe_thread
    from forum_extractor.browser import BrowserManager

    async def go():
        b = BrowserManager(timeout=20, max_retries=0)
        try:
            ok = await probe_thread(b, env.server.base + "/xenforo2/index.php?threads/just-checking-in.4/", None,
                                   env.cfg("/xenforo2/"))
            wall = await probe_thread(b, env.server.base + "/xenforo2/index.php?threads/members-only.99/", None,
                                     env.cfg("/xenforo2/"))
            return ok, wall
        finally:
            await b.close()
    ok, wall = asyncio.run(go())
    assert ok["ok"] and ok["text"] == expected_text(BY_ID[4])
    assert not wall["ok"] and "access restricted" in wall["error"]


def parse_post_records(out_dir: Path) -> list[dict]:
    recs = []
    for f in sorted(out_dir.glob("*.txt")):
        for m in re.finditer(r"={80}\nPOST NUMBER: (\d+)\nTHREAD TITLE: (.*?)\nTHREAD URL: (.*?)\nSTATUS: .*?\n"
                             r"ORIGINAL POST:\n(.*?)\n={80}\n", f.read_text(encoding="utf-8"), re.S):
            recs.append({"file": f.name, "n": int(m.group(1)), "title": m.group(2), "text": m.group(4)})
    return recs


def test_reddit_counted_by_posts(env):
    async def go():
        return await env.run(env.cfg("/reddit/r/NoFap/", posts_per_file=4, max_posts=7, file_prefix="Reddit_Posts",
                                     max_concurrency=1))
    m, job = asyncio.run(go())
    st = m.status(job)
    assert st["status"] == "completed" and st["threads_completed"] == 7, st
    recs = parse_post_records(env.out)
    assert [r["n"] for r in recs] == list(range(1, 8))
    assert sorted(p.name for p in env.out.iterdir()) == ["Reddit_Posts_00001-00004.txt", "Reddit_Posts_00005-00007.txt"]
    assert recs[0]["title"] == "Forum rules - please read before posting"
    assert recs[1]["text"] == expected_text(BY_ID[2])


def test_reddit_all_posts_one_file(env):
    async def go():
        return await env.run(env.cfg("/reddit/r/NoFap/", posts_per_file=500, file_prefix="Reddit_Posts"))
    m, job = asyncio.run(go())
    assert m.status(job)["status"] == "completed"
    assert [p.name for p in env.out.iterdir()] == ["Reddit_Posts_00001-00011.txt"]
    assert len(parse_post_records(env.out)) == 11
