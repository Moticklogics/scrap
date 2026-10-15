"""Web API tests (FastAPI TestClient + real Chromium against the fixture server)."""
import time

import pytest
from fastapi.testclient import TestClient

from forum_extractor.web.app import create_app

pytestmark = pytest.mark.browser


@pytest.fixture
def client(tmp_path):
    app = create_app(str(tmp_path / "data"))
    with TestClient(app) as c:
        yield c


def cfg(server, path, tmp_path, **kw):
    d = {"start_url": server.base + path, "output_dir": str(tmp_path / "out"), "request_delay": 0,
         "pages_per_file": 1100, "max_concurrency": 2}
    d.update(kw)
    return d


def test_analyze_preview_profile_and_job(client, fresh_server, tmp_path):
    c = cfg(fresh_server, "/xenforo2/index.php?forums/recovery.2/page-2", tmp_path)
    r = client.post("/api/analyze", json={"config": c})
    assert r.status_code == 200, r.text
    res = r.json()
    an = res["analysis"]
    assert an["page_type"] == "listing" and an["platform"] == "xenforo2" and an["page_number"] == 2
    assert an["threads"]["count"] == 4
    assert an["pagination"]["next_url"].endswith("page-3")
    pv = res["preview"]
    assert pv["ok"] and pv["confidence"] >= 0.9 and pv["text"].startswith("Welcome to the recovery forum")
    assert res["suggested_profile"]["post_container_selector"] == "article.message--post"

    # test-thread with a user selector override
    r = client.post("/api/test-thread", json={
        "config": c, "url": fresh_server.base + "/xenforo2/index.php?threads/my-tracking-script.7/",
        "profile": {"post_container_selector": "article.message--post", "post_body_selector": ".bbWrapper",
                    "exclude_selectors": [".bbCodeBlock--code > .bbCodeBlock-title"]}})
    assert r.json()["ok"] and r.json()["method"] == "profile"

    # save + read profile
    prof = dict(res["suggested_profile"], notes="checked")
    assert client.put("/api/profiles/127.0.0.1", json=prof).json()["source"] == "user"
    assert client.get("/api/profiles/127.0.0.1").json()["notes"] == "checked"

    # run the job
    job = client.post("/api/jobs", json={"config": c, "resume_previous": True}).json()["job_id"]
    for _ in range(300):
        st = client.get(f"/api/jobs/{job}").json()
        if not st["running"] and st["status"] not in ("created", "running"):
            break
        time.sleep(0.2)
    assert st["status"] == "completed", st
    assert st["threads_completed"] == 7 and st["threads_duplicate"] == 1
    assert st["output_files"][0].endswith("Forum_Pages_0002-0003.txt")
    dl = client.get(f"/api/jobs/{job}/files/0")
    assert dl.status_code == 200 and "Forum_Pages_0002-0003.txt" in dl.headers["content-disposition"]
    assert dl.text.count("ORIGINAL POST:") == 7
    assert client.get(f"/api/jobs/{job}/files/5").status_code == 404
    tail = client.get(f"/api/jobs/{job}/output-preview").json()
    assert "ORIGINAL POST:" in tail["text"]
    log = client.get(f"/api/jobs/{job}/log?format=jsonl")
    assert log.status_code == 200 and "job_start" in log.text
    csv = client.get(f"/api/jobs/{job}/log?format=csv")
    assert "thread_url" in csv.text.splitlines()[0]
    assert any(j["id"] == job for j in client.get("/api/jobs").json())


def test_resume_previous_session_reuses_job(client, fresh_server, tmp_path):
    c = cfg(fresh_server, "/phpbb/viewforum.php?f=2", tmp_path, max_concurrency=1, request_delay=0.3)
    job = client.post("/api/jobs", json={"config": c}).json()["job_id"]
    time.sleep(1.5)
    client.post(f"/api/jobs/{job}/stop")
    for _ in range(200):
        st = client.get(f"/api/jobs/{job}").json()
        if not st["running"]:
            break
        time.sleep(0.1)
    assert st["status"] in ("stopped", "completed")
    r = client.post("/api/jobs", json={"config": dict(c, request_delay=0), "resume_previous": True}).json()
    assert r["job_id"] == job and r["resumed"]
    for _ in range(300):
        st = client.get(f"/api/jobs/{job}").json()
        if not st["running"]:
            break
        time.sleep(0.2)
    assert st["status"] == "completed" and st["threads_completed"] == 10


def test_bad_input(client):
    assert client.post("/api/analyze", json={"config": {"start_url": "ftp://x"}}).status_code == 400
    assert client.get("/api/jobs/nope").status_code == 404
    assert client.get("/").status_code == 200


def test_live_progress_cancel_and_delete(client, fresh_server, tmp_path):
    c = cfg(fresh_server, "/vanilla/categories/general", tmp_path, max_concurrency=1, request_delay=0.4)
    job = client.post("/api/jobs", json={"config": c, "resume_previous": False}).json()["job_id"]
    seen_activity = None
    for _ in range(100):
        st = client.get(f"/api/jobs/{job}").json()
        if st["activity"] and "thread" in st["activity"]:
            seen_activity = st["activity"]
            break
        time.sleep(0.1)
    assert seen_activity and st["page_progress"]["total"] == 4
    listed = next(j for j in client.get("/api/jobs").json() if j["id"] == job)
    assert listed["running"] and listed["activity"]
    client.post(f"/api/jobs/{job}/stop")  # Cancel
    for _ in range(200):
        st = client.get(f"/api/jobs/{job}").json()
        if not st["running"]:
            break
        time.sleep(0.1)
    assert st["status"] == "stopped"
    assert any(e["event"] == "extracted" for e in st["recent_events"]) or st["threads_completed"] == 0
    files = st["output_files"]
    r = client.delete(f"/api/jobs/{job}?files=true").json()
    assert r["deleted"] == job
    assert client.get(f"/api/jobs/{job}").status_code == 404
    import os
    assert not any(os.path.exists(f) for f in files)


def test_delete_running_job(client, fresh_server, tmp_path):
    c = cfg(fresh_server, "/nodebb/category/2/general-discussion", tmp_path, request_delay=0.5)
    job = client.post("/api/jobs", json={"config": c, "resume_previous": False}).json()["job_id"]
    time.sleep(1.0)
    assert client.delete(f"/api/jobs/{job}").json()["deleted"] == job  # keeps files by default
    assert all(j["id"] != job for j in client.get("/api/jobs").json())


def test_ignore_robots_button(client, fresh_server, tmp_path):
    c = cfg(fresh_server, "/private/forum", tmp_path)
    job = client.post("/api/jobs", json={"config": c, "resume_previous": False}).json()["job_id"]
    for _ in range(100):
        st = client.get(f"/api/jobs/{job}").json()
        if not st["running"] and st["status"] != "created":
            break
        time.sleep(0.1)
    assert st["status"] == "blocked" and "robots.txt" in st["message"]
    client.post(f"/api/jobs/{job}/ignore_robots")
    for _ in range(150):
        st = client.get(f"/api/jobs/{job}").json()
        if not st["running"]:
            break
        time.sleep(0.1)
    assert st["config"]["respect_robots"] is False
    assert st["status"] != "blocked" or "robots" not in st["message"]
    assert any(e["event"] == "robots_ignored" for e in st["recent_events"])


def test_reddit_preview(client, fresh_server, tmp_path):
    c = cfg(fresh_server, "/rs/r/NoFap/new/", tmp_path, source="reddit")
    res = client.post("/api/analyze", json={"config": c}).json()
    an = res["analysis"]
    assert an["platform"] == "reddit" and an["threads"]["count"] == 4  # image post and ad excluded
    assert res["reddit"]["api_mode"] is False
    assert res["preview"]["ok"] and res["preview"]["text"].startswith("Welcome to the recovery forum")
    pv = client.post("/api/test-thread", json={"config": c, "url": an["threads"]["items"][1]["url"]}).json()
    assert pv["ok"] and pv["title"] == "Day 1: starting my journey"


def test_reddit_api_key_endpoints(client, fresh_server, tmp_path):
    assert client.get("/api/reddit-credentials").json()["configured"] is False
    test_url = fresh_server.base + "/rj/r/NoFap/new/"
    bad = client.put("/api/reddit-credentials", json={"client_id": "goodid", "client_secret": "nope", "test_url": test_url})
    assert bad.status_code == 400 and "did not accept" in bad.json()["detail"]
    ok = client.put("/api/reddit-credentials", json={"client_id": "goodid", "client_secret": "goodsecret",
                                                     "username": "u/tester", "test_url": test_url})
    assert ok.status_code == 200, ok.text
    info = client.get("/api/reddit-credentials").json()
    assert info == {"configured": True, "client_id": "goodid", "username": "tester"}  # secret never returned
    c = cfg(fresh_server, "/rj/r/NoFap/new/", tmp_path, source="reddit")
    res = client.post("/api/analyze", json={"config": c}).json()
    assert res["reddit"]["api_mode"] is True and res["analysis"]["threads"]["count"] == 4
    assert client.delete("/api/reddit-credentials").json()["configured"] is False
