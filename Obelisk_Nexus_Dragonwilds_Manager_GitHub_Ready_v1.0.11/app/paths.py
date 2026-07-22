from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "DragonwildsServerManagerRebuild"


def data_root() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    root = base / APP_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def ensure_dirs() -> dict[str, Path]:
    root = data_root()
    result = {"root": root}
    for name in ("logs", "cache", "downloads", "manifests", "temp", "diagnostics", "tools"):
        p = root / name
        p.mkdir(parents=True, exist_ok=True)
        result[name] = p
    return result
