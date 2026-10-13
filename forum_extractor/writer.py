"""Incremental, rotating UTF-8 text output (one file per N listing pages)."""
from __future__ import annotations

import os
from pathlib import Path

from .storage import Database

SEP = "=" * 80


class WriterError(Exception):
    """Disk / file-system failure while writing output."""


def format_record(page_number: int | None, title: str, url: str, text: str,
                  status: str = "Successfully extracted") -> str:
    page = page_number if page_number is not None else "N/A"
    return (
        f"{SEP}\n"
        f"LISTING PAGE: {page}\n"
        f"THREAD TITLE: {title}\n"
        f"THREAD URL: {url}\n"
        f"STATUS: {status}\n"
        f"ORIGINAL POST:\n"
        f"{text.rstrip()}\n"
        f"{SEP}\n\n"
    )


def batch_filename(prefix: str, first: int, last: int) -> str:
    width = max(4, len(str(last)))
    return f"{prefix}_{first:0{width}d}-{last:0{width}d}.txt"


class OutputWriter:
    """Writes records to `<prefix>_<first>-<last>.txt`, one file per `pages_per_file` listing pages.

    A batch is identified by the listing-page sequence index (0-based position of the page within the job),
    so numbering stays correct even when page numbers are inferred. The file is opened with the planned
    range; when the batch closes early (end of forum) it is renamed to the range actually processed.
    Nothing is buffered beyond one record: every record is flushed, and fsync'ed at page boundaries.
    """

    def __init__(self, db: Database, job_id: str, output_dir: str | Path, prefix: str, pages_per_file: int):
        self.db = db
        self.job_id = job_id
        self.dir = Path(output_dir).expanduser()
        self.prefix = prefix
        self.n = max(1, int(pages_per_file))
        self._fh = None
        self._fh_path: Path | None = None
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise WriterError(f"cannot create output folder {self.dir}: {exc}") from exc

    def batch_index(self, seq: int) -> int:
        return seq // self.n

    def path_for(self, seq: int, page_number: int | None) -> Path:
        idx = self.batch_index(seq)
        b = self.db.get_batch(self.job_id, idx)
        if b is None:
            first = page_number if page_number is not None else seq + 1
            planned = first + self.n - 1
            name = batch_filename(self.prefix, first, planned)
            path = self.dir / name
            k = 2
            while path.exists():  # never append to a file that belongs to another job
                path = self.dir / name.replace(".txt", f"_{k}.txt")
                k += 1
            self.db.save_batch(self.job_id, idx, first_page=first, last_page=first, planned_last=planned,
                               filename=str(path), closed=0)
            return path
        return Path(b["filename"])

    def _open(self, path: Path):
        if self._fh_path != path:
            self.close()
            try:
                self._fh = open(path, "a", encoding="utf-8", newline="\n")
            except OSError as exc:
                raise WriterError(f"cannot open {path}: {exc}") from exc
            self._fh_path = path
        return self._fh

    def write(self, seq: int, page_number: int | None, title: str, url: str, text: str) -> Path:
        path = self.path_for(seq, page_number)
        fh = self._open(path)
        try:
            fh.write(format_record(page_number, title, url, text))
            fh.flush()
        except OSError as exc:
            raise WriterError(f"write failed for {path}: {exc}") from exc
        return path

    def page_done(self, seq: int, page_number: int | None) -> None:
        """Record page completion; fsync; close the batch when it is full."""
        idx = self.batch_index(seq)
        path = self.path_for(seq, page_number)
        if not path.exists():
            path.touch()
        if page_number is not None:
            self.db.save_batch(self.job_id, idx, last_page=page_number)
        self.sync()
        if seq % self.n == self.n - 1:
            self._close_batch(idx)

    def sync(self) -> None:
        if self._fh is not None:
            try:
                self._fh.flush()
                os.fsync(self._fh.fileno())
            except OSError as exc:
                raise WriterError(f"fsync failed for {self._fh_path}: {exc}") from exc

    def _close_batch(self, idx: int) -> None:
        b = self.db.get_batch(self.job_id, idx)
        if not b or b["closed"]:
            return
        old = Path(b["filename"])
        if self._fh_path == old:
            self.close()
        new_name = batch_filename(self.prefix, b["first_page"], b["last_page"])
        new = old.with_name(new_name)
        final = old
        if new != old and b["last_page"] != b["planned_last"] and old.exists() and not new.exists():
            try:
                old.rename(new)
                final = new
                self.db.rename_output(self.job_id, str(old), str(new))
            except OSError:
                final = old
        self.db.save_batch(self.job_id, idx, filename=str(final), closed=1)

    def finalize(self) -> None:
        """Called when the end of the forum (or max pages) is reached: close every open batch."""
        self.close()
        for b in self.db.batches(self.job_id):
            if not b["closed"]:
                self._close_batch(b["idx"])

    def close(self) -> None:
        if self._fh is not None:
            try:
                self._fh.flush()
                os.fsync(self._fh.fileno())
            except OSError:
                pass
            try:
                self._fh.close()
            except OSError:
                pass
        self._fh = None
        self._fh_path = None

    def current_file(self) -> str:
        return str(self._fh_path) if self._fh_path else ""
