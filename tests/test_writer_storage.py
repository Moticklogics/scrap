from pathlib import Path

from forum_extractor.storage import Database
from forum_extractor.writer import SEP, OutputWriter, batch_filename, format_record


def test_record_format_exact():
    rec = format_record(2, "Café ☕", "https://f/t/1", "Line 1\n\nLine 2\n")
    assert rec == (f"{SEP}\nLISTING PAGE: 2\nTHREAD TITLE: Café ☕\nTHREAD URL: https://f/t/1\n"
                   f"STATUS: Successfully extracted\nORIGINAL POST:\nLine 1\n\nLine 2\n{SEP}\n\n")
    assert len(SEP) == 80


def test_batch_names():
    assert batch_filename("Forum_Pages", 2, 1101) == "Forum_Pages_0002-1101.txt"
    assert batch_filename("Forum_Pages", 1102, 2201) == "Forum_Pages_1102-2201.txt"
    assert batch_filename("Forum_Pages", 9999, 11098) == "Forum_Pages_09999-11098.txt"


def _write_pages(w, pages, start=2):
    for seq in range(pages):
        num = start + seq
        w.write(seq, num, f"T{num}", f"https://f/t/{num}", f"post on page {num} – ünïcödé 你好")
        w.page_done(seq, num)


def test_rotation_and_final_rename(tmp_path):
    db = Database(tmp_path / "s.db")
    job = db.create_job("https://f/", {})
    w = OutputWriter(db, job, tmp_path / "out", "Forum_Pages", 3)
    _write_pages(w, 7)  # pages 2..8 with 3 pages per file
    w.finalize()
    names = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert names == ["Forum_Pages_0002-0004.txt", "Forum_Pages_0005-0007.txt", "Forum_Pages_0008-0008.txt"]
    text = (tmp_path / "out" / "Forum_Pages_0005-0007.txt").read_text(encoding="utf-8")
    assert text.count("LISTING PAGE:") == 3 and "LISTING PAGE: 5" in text and "你好" in text
    # strict UTF-8
    (tmp_path / "out" / "Forum_Pages_0002-0004.txt").read_bytes().decode("utf-8")


def test_incomplete_batch_keeps_planned_name_until_finalized(tmp_path):
    db = Database(tmp_path / "s.db")
    job = db.create_job("https://f/", {})
    w = OutputWriter(db, job, tmp_path / "out", "Forum_Pages", 1100)
    _write_pages(w, 2)
    w.close()
    assert [p.name for p in (tmp_path / "out").iterdir()] == ["Forum_Pages_0002-1101.txt"]
    # resume with a new writer appends to the same file
    w2 = OutputWriter(db, job, tmp_path / "out", "Forum_Pages", 1100)
    w2.write(2, 4, "T4", "https://f/t/4", "x")
    w2.page_done(2, 4)
    w2.finalize()
    files = list((tmp_path / "out").iterdir())
    assert [p.name for p in files] == ["Forum_Pages_0002-0004.txt"]
    assert files[0].read_text(encoding="utf-8").count("LISTING PAGE:") == 3


def test_new_job_never_appends_to_foreign_file(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "Forum_Pages_0001-1100.txt").write_text("someone else's data", encoding="utf-8")
    db = Database(tmp_path / "s.db")
    job = db.create_job("https://f/", {})
    w = OutputWriter(db, job, out, "Forum_Pages", 1100)
    p = w.write(0, 1, "T", "u", "x")
    assert p.name == "Forum_Pages_0001-1100_2.txt"
    assert (out / "Forum_Pages_0001-1100.txt").read_text(encoding="utf-8") == "someone else's data"


def test_database_resume_state(tmp_path):
    db = Database(tmp_path / "s.db")
    job = db.create_job("https://f/", {"a": 1})
    db.upsert_page(job, 0, "https://f/p1", 1, "done", next_url="https://f/p2")
    t = db.add_thread(job, 0, 1, "https://f/p1", 0, "https://f/t/1", "k1", "T1")
    assert t["status"] == "pending"
    again = db.add_thread(job, 0, 1, "https://f/p1", 0, "https://f/t/1", "k1", "T1")
    assert again["id"] == t["id"]  # idempotent: no duplicate rows on resume
    db.update_thread(t["id"], status="done")
    assert db.thread_counts(job)["done"] == 1
    db.update_job(job, status="running")
    assert db.mark_interrupted_jobs() == 1
    assert db.get_job(job)["status"] == "interrupted"
