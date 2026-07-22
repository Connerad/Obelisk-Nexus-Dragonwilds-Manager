from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Iterable
from datetime import datetime, timezone

from .models import ServerProfile
from .paths import data_root
from .secrets import protect, unprotect


class ProfileStore:
    def __init__(self, path: Path | None = None):
        self.path = path or data_root() / "profiles.json"
        self._lock = threading.RLock()

    def load(self) -> list[ServerProfile]:
        with self._lock:
            if not self.path.exists():
                return []
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(raw, list):
                    raise ValueError("profiles.json must contain a list")
                return [self._decode_profile(item) for item in raw if isinstance(item, dict)]
            except Exception:
                recovery = self.path.with_suffix(".recovery.json")
                if recovery.exists():
                    raw = json.loads(recovery.read_text(encoding="utf-8"))
                    return [self._decode_profile(item) for item in raw if isinstance(item, dict)]
                raise

    def save(self, profiles: Iterable[ServerProfile]) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps([self._encode_profile(p) for p in profiles], indent=2, ensure_ascii=False)
            recovery = self.path.with_suffix(".recovery.json")
            if self.path.exists():
                recovery.write_bytes(self.path.read_bytes())
            fd, tmp_name = tempfile.mkstemp(prefix=self.path.name, suffix=".tmp", dir=self.path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(payload)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp_name, self.path)
            finally:
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)


    @staticmethod
    def _encode_profile(profile: ServerProfile) -> dict:
        data = profile.to_dict()
        for key in ("admin_password", "world_password", "discord_webhook"):
            raw = str(data.get(key) or "")
            data[key] = protect(raw) if raw else ""
        return data

    @staticmethod
    def _decode_profile(data: dict) -> ServerProfile:
        decoded = dict(data)
        for key in ("admin_password", "world_password", "discord_webhook"):
            raw = str(decoded.get(key) or "")
            if raw.startswith(("dpapi:", "plain-local:")):
                decoded[key] = unprotect(raw)
        return ServerProfile.from_dict(decoded)

    @staticmethod
    def stamp(profile: ServerProfile, new: bool = False) -> None:
        now = datetime.now(timezone.utc).isoformat()
        if new or not profile.created_at:
            profile.created_at = now
        profile.updated_at = now
