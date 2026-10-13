"""Persistent per-forum site profiles stored as JSON files (one per domain)."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .config import SiteProfile
from .storage import now_iso


def _safe_name(domain: str) -> str:
    return re.sub(r"[^a-z0-9.-]+", "_", domain.lower()) or "default"


class ProfileStore:
    def __init__(self, directory: str | Path):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)

    def path(self, domain: str) -> Path:
        return self.dir / f"{_safe_name(domain)}.json"

    def get(self, domain: str) -> SiteProfile | None:
        p = self.path(domain)
        if not p.exists():
            return None
        try:
            return SiteProfile.from_dict(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return None

    def save(self, profile: SiteProfile) -> SiteProfile:
        profile.updated_at = now_iso()
        tmp = self.path(profile.domain).with_suffix(".tmp")
        tmp.write_text(json.dumps(profile.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path(profile.domain))
        return profile

    def save_auto(self, profile: SiteProfile) -> SiteProfile:
        """Save a generated profile without overwriting one the user has edited."""
        existing = self.get(profile.domain)
        if existing and existing.source == "user":
            return existing
        profile.source = "auto"
        return self.save(profile)

    def delete(self, domain: str) -> bool:
        p = self.path(domain)
        if p.exists():
            p.unlink()
            return True
        return False

    def list(self) -> list[SiteProfile]:
        out = []
        for p in sorted(self.dir.glob("*.json")):
            try:
                out.append(SiteProfile.from_dict(json.loads(p.read_text(encoding="utf-8"))))
            except (OSError, ValueError):
                continue
        return out
