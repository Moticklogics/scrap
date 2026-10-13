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
