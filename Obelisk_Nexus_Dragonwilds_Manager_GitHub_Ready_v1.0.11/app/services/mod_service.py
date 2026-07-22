from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .backup_service import create_backup


class ModInstallError(RuntimeError):
    pass


EXECUTABLE_EXTENSIONS = {".exe", ".com", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".msi", ".scr"}
MAX_ARCHIVE_FILES = 20_000
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024 * 1024


def _safe_member(name: str) -> Path:
    normalized = name.replace("\\", "/")
    path = Path(normalized)
    if path.is_absolute() or ".." in path.parts or normalized.startswith("/"):
        raise ModInstallError(f"Unsafe archive path: {name}")
    return path


def _zip_is_link(info: zipfile.ZipInfo) -> bool:
    # Unix file type bits are stored in the upper 16 external-attribute bits.
    return ((info.external_attr >> 16) & 0o170000) == 0o120000


def list_archive(archive: Path) -> list[str]:
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive, "r") as zf:
            infos = zf.infolist()
            if len(infos) > MAX_ARCHIVE_FILES:
                raise ModInstallError("The archive contains too many files.")
            if sum(max(0, info.file_size) for info in infos) > MAX_ARCHIVE_BYTES:
                raise ModInstallError("The archive expands beyond the allowed size limit.")
            for info in infos:
                if _zip_is_link(info):
                    raise ModInstallError(f"Archive links are not allowed: {info.filename}")
            return [info.filename for info in infos]
    # Windows 11 bsdtar can read many 7Z/RAR archives. List verbosely first and
    # reject symbolic/hard links before extraction so they cannot redirect writes.
    verbose = subprocess.run(["tar", "-tvf", str(archive)], capture_output=True, text=True, timeout=60, check=False)
    if verbose.returncode != 0:
        raise ModInstallError("Unsupported archive. ZIP is always supported; 7Z/RAR require Windows tar/libarchive support.")
    rows = [line for line in verbose.stdout.splitlines() if line.strip()]
    if len(rows) > MAX_ARCHIVE_FILES:
        raise ModInstallError("The archive contains too many files.")
    for row in rows:
        mode = row.lstrip()[:1]
        if mode in {"l", "h"}:
            raise ModInstallError("Archive links are not allowed.")
    result = subprocess.run(["tar", "-tf", str(archive)], capture_output=True, text=True, timeout=60, check=False)
    if result.returncode != 0:
        raise ModInstallError("Archive listing failed.")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _validate_extracted_tree(target: Path) -> None:
    target_resolved = target.resolve()
    count = 0
    total = 0
    for current, dirs, files in os.walk(target, followlinks=False):
        current_path = Path(current)
        for name in [*dirs, *files]:
            item = current_path / name
            if item.is_symlink():
                raise ModInstallError(f"Archive links are not allowed: {item.name}")
            try:
                item.resolve().relative_to(target_resolved)
            except ValueError as exc:
                raise ModInstallError(f"Extracted path escaped the staging directory: {item}") from exc
        for name in files:
            item = current_path / name
            count += 1
            total += item.stat().st_size
            if count > MAX_ARCHIVE_FILES or total > MAX_ARCHIVE_BYTES:
                raise ModInstallError("The extracted archive exceeds the safety limits.")


def extract_archive(archive: Path, target: Path) -> None:
    names = list_archive(archive)
    for name in names:
        member = _safe_member(name)
        if member.suffix.lower() in EXECUTABLE_EXTENSIONS:
            raise ModInstallError(f"The archive contains an executable or script: {name}")
    target.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive, "r") as zf:
            for info in zf.infolist():
                _safe_member(info.filename)
                if _zip_is_link(info):
                    raise ModInstallError(f"Archive links are not allowed: {info.filename}")
                zf.extract(info, target)
        _validate_extracted_tree(target)
        return
    result = subprocess.run(["tar", "-xf", str(archive), "-C", str(target)], capture_output=True, text=True, timeout=300, check=False)
    if result.returncode != 0:
        raise ModInstallError(result.stderr.strip() or "Archive extraction failed.")
    _validate_extracted_tree(target)


def detect_destination(server_root: Path, relative: Path) -> Path | None:
    parts = [p.lower() for p in relative.parts]
    name = relative.name.lower()
    if relative.suffix.lower() == ".pak":
        return server_root / "RSDragonwilds" / "Content" / "Paks" / "~mods" / relative.name
    if "mods" in parts and (relative.suffix.lower() in {".lua", ".dll", ".json", ".ini"} or "scripts" in parts):
        idx = parts.index("mods")
        tail = Path(*relative.parts[idx + 1:]) if idx + 1 < len(relative.parts) else Path(relative.name)
        return server_root / "RSDragonwilds" / "Binaries" / "Win64" / "Mods" / tail
    if name == "mods.txt":
        return server_root / "RSDragonwilds" / "Binaries" / "Win64" / "Mods" / "mods.txt"
    # Archives already rooted at RSDragonwilds may be copied by relative layout.
    if "rsdragonwilds" in parts:
        idx = parts.index("rsdragonwilds")
        return server_root / Path(*relative.parts[idx:])
    return None


def _manifest_dir(server_root: Path) -> Path:
    path = server_root / ".dwsm" / "mods"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_manifests(server_root: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for path in _manifest_dir(server_root).glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            out[path.stem] = data
        except Exception:
            continue
    return out


def install_mod(server_root: Path, backup_dir: Path, archive: Path, mod_id: int, name: str, version: str) -> Path:
    server_root = server_root.resolve()
    if not archive.is_file():
        raise FileNotFoundError(archive)
    # A mod install must never proceed if its safety backup fails.
    important = [p for p in [
        server_root / "RSDragonwilds" / "Content" / "Paks" / "~mods",
        server_root / "RSDragonwilds" / "Binaries" / "Win64" / "Mods",
    ] if p.exists()]
    if important:
        create_backup(server_root, backup_dir, important, f"pre-mod-{mod_id}")

    staging = Path(tempfile.mkdtemp(prefix="dwsm-mod-"))
    rollback = staging / ".rollback"
    copied: list[tuple[Path, Path | None]] = []
    try:
        extract_archive(archive, staging / "content")
        files = [p for p in (staging / "content").rglob("*") if p.is_file()]
        mappings: list[tuple[Path, Path]] = []
        for source in files:
            relative = source.relative_to(staging / "content")
            destination = detect_destination(server_root, relative)
            if destination:
                mappings.append((source, destination))
        if not mappings:
            raise ModInstallError("No supported server PAK or UE4SS files were found in this archive.")

        manifests = load_manifests(server_root)
        ownership: dict[str, str] = {}
        for owner_id, manifest in manifests.items():
            if owner_id == str(mod_id):
                continue
            for item in manifest.get("files", []):
                ownership[str(item.get("path", "")).casefold()] = owner_id
        for _, destination in mappings:
            rel = destination.resolve().relative_to(server_root).as_posix()
            owner = ownership.get(rel.casefold())
            if owner:
                raise ModInstallError(f"File conflict: {rel} is owned by installed mod {owner}.")

        manifest_files: list[dict] = []
        for source, destination in mappings:
            destination.parent.mkdir(parents=True, exist_ok=True)
            old = None
            if destination.exists():
                rel = destination.resolve().relative_to(server_root)
                old = rollback / rel
                old.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(destination, old)
            shutil.copy2(source, destination)
            copied.append((destination, old))
            manifest_files.append({
                "path": destination.resolve().relative_to(server_root).as_posix(),
                "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
                "size": destination.stat().st_size,
            })
        manifest = {
            "mod_id": int(mod_id), "name": name, "version": version,
            "installed_at": datetime.now(timezone.utc).isoformat(), "files": manifest_files,
        }
        manifest_path = _manifest_dir(server_root) / f"{int(mod_id)}.json"
        temp_manifest = manifest_path.with_suffix(".tmp")
        temp_manifest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        temp_manifest.replace(manifest_path)
        return manifest_path
    except Exception:
        for destination, old in reversed(copied):
            if old and old.exists():
                shutil.copy2(old, destination)
            else:
                destination.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def uninstall_mod(server_root: Path, backup_dir: Path, mod_id: int) -> None:
    server_root = server_root.resolve()
    manifest_path = _manifest_dir(server_root) / f"{int(mod_id)}.json"
    if not manifest_path.exists():
        raise ModInstallError("The installation manifest is missing.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = [server_root / item["path"] for item in manifest.get("files", [])]
    existing = [p for p in files if p.exists()]
    if existing:
        create_backup(server_root, backup_dir, existing, f"pre-mod-remove-{mod_id}")
    for path in existing:
        path.unlink()
    manifest_path.unlink()
