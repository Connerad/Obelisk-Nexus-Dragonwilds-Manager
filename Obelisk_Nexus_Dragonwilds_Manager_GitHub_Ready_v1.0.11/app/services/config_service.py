from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


CONFIG_EXTENSIONS = {".ini", ".cfg", ".conf", ".json", ".yaml", ".yml", ".toml", ".txt", ".xml", ".properties", ".env", ".lua"}
MAX_CONFIG_SIZE = 4 * 1024 * 1024
DEDICATED_SECTION = "/Script/Dominion.DedicatedServerSettings"


@dataclass(slots=True)
class IniEntry:
    section: str
    key: str
    value: str
    line_index: int


class IniDocument:
    """Line-preserving INI editor. Comments, blank lines and unknown keys survive saves."""

    _section_re = re.compile(r"^\s*\[([^]]+)]\s*$")
    _key_re = re.compile(r"^\s*([^#;][^=]*?)\s*=\s*(.*?)\s*$")

    def __init__(self, text: str):
        self.newline = "\r\n" if "\r\n" in text else "\n"
        self.lines = text.splitlines()

    @classmethod
    def load(cls, path: Path) -> "IniDocument":
        return cls(path.read_text(encoding="utf-8-sig", errors="replace"))

    def entries(self) -> list[IniEntry]:
        section = ""
        out: list[IniEntry] = []
        for idx, line in enumerate(self.lines):
            match = self._section_re.match(line)
            if match:
                section = match.group(1).strip()
                continue
            match = self._key_re.match(line)
            if match:
                out.append(IniEntry(section, match.group(1).strip(), match.group(2), idx))
        return out

    def set(self, section: str, key: str, value: str) -> None:
        target_section = section.casefold()
        target_key = key.casefold()
        entries = self.entries()
        for entry in entries:
            if entry.section.casefold() == target_section and entry.key.casefold() == target_key:
                prefix = self.lines[entry.line_index].split("=", 1)[0]
                self.lines[entry.line_index] = f"{prefix}={value}"
                return

        # Locate the exact target section. Never append under a different section.
        section_start = None
        section_end = len(self.lines)
        current = ""
        for idx, line in enumerate(self.lines):
            match = self._section_re.match(line)
            if not match:
                continue
            name = match.group(1).strip()
            if current.casefold() == target_section and section_start is not None:
                section_end = idx
                break
            current = name
            if current.casefold() == target_section:
                section_start = idx
        if section_start is not None:
            self.lines.insert(section_end, f"{key}={value}")
            return

        if self.lines and self.lines[-1].strip():
            self.lines.append("")
        self.lines.extend([f"[{section}]", f"{key}={value}"])

    def render(self) -> str:
        return self.newline.join(self.lines) + self.newline


def discover_configs(root: Path) -> list[Path]:
    """Find server and mod text configs without crawling the entire game payload.

    Known config roots are scanned recursively, while the installation root is
    scanned only two levels deep. This includes UE4SS mod configurations but
    avoids multi-gigabyte Content/Engine trees that previously froze managers.
    """
    if not root.exists():
        return []
    root = root.resolve()
    game_root = root if root.name.casefold() == "rsdragonwilds" else root / "RSDragonwilds"
    deep_roots = [
        game_root / "Saved" / "Config",
        game_root / "Binaries" / "Win64" / "Mods",
        root / "Mods",
        root / "config",
        root / ".dwsm",
    ]
    results: set[Path] = set()

    def consider(path: Path) -> None:
        if len(results) >= 1000 or path.is_symlink() or path.suffix.lower() not in CONFIG_EXTENSIONS:
            return
        try:
            resolved = path.resolve()
            resolved.relative_to(root)
            if resolved.is_file() and resolved.stat().st_size <= MAX_CONFIG_SIZE:
                results.add(resolved)
        except (OSError, ValueError):
            return

    # Bounded shallow scan for nonstandard top-level configuration folders.
    for current, dirs, files in __import__("os").walk(root):
        current_path = Path(current)
        try:
            depth = len(current_path.relative_to(root).parts)
        except ValueError:
            continue
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in {"Content", "Engine", "steamapps"}]
        if depth >= 2:
            dirs[:] = []
        for name in files:
            consider(current_path / name)

    # Recursively scan only the directories where Dragonwilds/mod configs live.
    for scan_root in deep_roots:
        if not scan_root.exists() or scan_root.is_symlink():
            continue
        for current, dirs, files in __import__("os").walk(scan_root):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for name in files:
                consider(Path(current) / name)
            if len(results) >= 1000:
                break
    return sorted(results, key=lambda path: str(path).casefold())


def backup_file(path: Path) -> Path | None:
    if not path.exists():
        return None
    backup_dir = path.parent / ".manager_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    target = backup_dir / f"{path.name}.{stamp}.bak"
    shutil.copy2(path, target)
    return target


def save_text_atomic(path: Path, text: str) -> Path | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = backup_file(path)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8", newline="")
    temp.replace(path)
    return backup


def ensure_dedicated_config(path: Path, values: dict[str, str]) -> None:
    """Create/update only the documented Dragonwilds dedicated-server keys.

    The line-preserving editor keeps ServerGuid, future keys, comments and all
    unrelated sections exactly where they are.
    """
    if path.exists():
        doc = IniDocument.load(path)
    else:
        doc = IniDocument(
            "; Managed by Dragonwilds Server Manager\n"
            "[SectionsToSave]\n"
            "bCanSaveAllSections=true\n\n"
            f"[{DEDICATED_SECTION}]\n"
        )
    for key, value in values.items():
        doc.set(DEDICATED_SECTION, key, str(value))
    save_text_atomic(path, doc.render())
