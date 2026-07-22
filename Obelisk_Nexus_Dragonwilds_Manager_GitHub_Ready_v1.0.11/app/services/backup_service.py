from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


class BackupError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(root: Path, path: Path) -> Path:
    root_resolved = root.resolve()
    path_resolved = path.resolve()
    try:
        return path_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise BackupError(f"Path is outside server root: {path}") from exc


def create_backup(server_root: Path, backup_dir: Path, sources: Iterable[Path], label: str = "full") -> Path:
    server_root = server_root.resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)
    existing: list[Path] = []
    for source in sources:
        if source.exists():
            existing.append(source)
    if not existing:
        raise BackupError("No backup source exists; no archive was created.")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    final = backup_dir / f"dragonwilds-{label}-{stamp}.zip"
    fd, temp_name = tempfile.mkstemp(prefix=final.name, suffix=".tmp", dir=backup_dir)
    os.close(fd)
    temp = Path(temp_name)
    manifest: list[dict[str, object]] = []
    file_count = 0
    try:
        with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for source in existing:
                if source.is_file():
                    rel = _safe_relative(server_root, source)
                    zf.write(source, rel.as_posix())
                    manifest.append({"path": rel.as_posix(), "size": source.stat().st_size})
                    file_count += 1
                else:
                    for item in source.rglob("*"):
                        if not item.is_file():
                            continue
                        # Never back up the destination folder into itself.
                        try:
                            item.resolve().relative_to(backup_dir.resolve())
                            continue
                        except ValueError:
                            pass
                        rel = _safe_relative(server_root, item)
                        zf.write(item, rel.as_posix())
                        manifest.append({"path": rel.as_posix(), "size": item.stat().st_size})
                        file_count += 1
            if file_count == 0:
                raise BackupError("Backup sources contained no files.")
            zf.writestr(".dwsm-manifest.json", json.dumps({
                "created_at": datetime.now(timezone.utc).isoformat(),
                "root": str(server_root),
                "files": manifest,
            }, indent=2))
        with zipfile.ZipFile(temp, "r") as zf:
            bad = zf.testzip()
            if bad:
                raise BackupError(f"Backup verification failed at {bad}")
        os.replace(temp, final)
        final.with_suffix(final.suffix + ".sha256").write_text(sha256(final) + "\n", encoding="ascii")
        return final
    except Exception:
        temp.unlink(missing_ok=True)
        raise


def verify_backup(archive: Path) -> tuple[bool, str]:
    sidecar = archive.with_suffix(archive.suffix + ".sha256")
    if not sidecar.exists():
        return False, "Checksum sidecar is missing."
    expected = sidecar.read_text(encoding="ascii", errors="ignore").strip().split()[0]
    actual = sha256(archive)
    if expected.lower() != actual.lower():
        return False, "SHA-256 mismatch."
    try:
        with zipfile.ZipFile(archive, "r") as zf:
            bad = zf.testzip()
            if bad:
                return False, f"ZIP contains a damaged file: {bad}"
            names = zf.namelist()
            if ".dwsm-manifest.json" not in names:
                return False, "Backup manifest is missing."
    except zipfile.BadZipFile:
        return False, "Not a valid ZIP archive."
    return True, "Backup verified."


def _validate_zip_names(zf: zipfile.ZipFile) -> None:
    for name in zf.namelist():
        normalized = name.replace("\\", "/")
        path = Path(normalized)
        if path.is_absolute() or ".." in path.parts or normalized.startswith("/"):
            raise BackupError(f"Unsafe archive path: {name}")


def restore_backup(server_root: Path, archive: Path, safety_backup_dir: Path) -> Path:
    ok, message = verify_backup(archive)
    if not ok:
        raise BackupError(message)
    server_root.mkdir(parents=True, exist_ok=True)
    safety = create_backup(server_root, safety_backup_dir, [server_root], "pre-restore") if any(server_root.iterdir()) else None
    staging = Path(tempfile.mkdtemp(prefix="dwsm-restore-"))
    changed: list[tuple[Path, Path | None]] = []
    try:
        with zipfile.ZipFile(archive, "r") as zf:
            _validate_zip_names(zf)
            zf.extractall(staging)
        for item in staging.rglob("*"):
            if not item.is_file() or item.name == ".dwsm-manifest.json":
                continue
            rel = item.relative_to(staging)
            target = server_root / rel
            old_copy = None
            if target.exists():
                old_copy = staging / ".rollback" / rel
                old_copy.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, old_copy)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
            changed.append((target, old_copy))
        return safety or archive
    except Exception:
        for target, old_copy in reversed(changed):
            if old_copy and old_copy.exists():
                shutil.copy2(old_copy, target)
            else:
                target.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
