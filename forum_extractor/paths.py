"""Location of the application's persistent state (database, profiles, logs)."""
from __future__ import annotations

import os
from pathlib import Path


def data_dir(override: str | None = None) -> Path:
    d = Path(override or os.environ.get("FORUM_EXTRACTOR_DATA", "data")).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d
