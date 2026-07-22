#!/usr/bin/env python3
from __future__ import annotations

import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "installer" / "payload.zip"

FILES = [
    ROOT / "DragonwildsServerManager.exe",
    ROOT / "DragonwildsServerManager.pyw",
    ROOT / "LICENSE",
    ROOT / "PRIVACY.md",
]
DIRECTORIES = [ROOT / "app", ROOT / "assets"]


def add_file(zf: zipfile.ZipFile, path: Path) -> None:
    if not path.exists():
        raise SystemExit(f"Required payload file is missing: {path.relative_to(ROOT)}")
    zf.write(path, path.relative_to(ROOT).as_posix())


def main() -> int:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUTPUT, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in FILES:
            add_file(zf, path)
        for directory in DIRECTORIES:
            for path in sorted(directory.rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts and not path.name.endswith((".pyc", ".pyo")):
                    add_file(zf, path)
    print(f"Created {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
