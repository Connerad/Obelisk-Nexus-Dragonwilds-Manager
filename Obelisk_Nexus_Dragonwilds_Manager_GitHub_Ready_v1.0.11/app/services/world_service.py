from __future__ import annotations

import shutil
from pathlib import Path
import os

from .backup_service import create_backup


def list_worlds(save_dir: Path) -> list[Path]:
    if not save_dir.exists():
        return []
    return sorted((p for p in save_dir.rglob("*.sav") if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)


def import_world(server_root: Path, save_dir: Path, source: Path, backup_dir: Path, replace_name: str | None = None) -> Path:
    if source.suffix.lower() != ".sav" or not source.is_file():
        raise ValueError("Choose a valid .sav world file.")
    save_dir.mkdir(parents=True, exist_ok=True)
    target = save_dir / (replace_name or source.name)
    if target.exists():
        # Destructive replacement is forbidden when the safety backup fails.
        create_backup(server_root, backup_dir, [target], "pre-world-import")
    temp = target.with_suffix(target.suffix + ".importing")
    shutil.copy2(source, temp)
    os.utime(temp, None)  # The server loads the newest .sav in the folder.
    if temp.stat().st_size == 0:
        temp.unlink(missing_ok=True)
        raise ValueError("The imported world file is empty.")
    temp.replace(target)
    return target


def delete_world(server_root: Path, world: Path, backup_dir: Path) -> Path:
    if not world.exists():
        raise FileNotFoundError(world)
    safety = create_backup(server_root, backup_dir, [world], "pre-world-delete")
    world.unlink()
    return safety


def clone_world(world: Path, new_name: str) -> Path:
    if not world.exists():
        raise FileNotFoundError(world)
    target = world.with_name(new_name if new_name.lower().endswith(".sav") else new_name + ".sav")
    if target.exists():
        raise FileExistsError(target)
    shutil.copy2(world, target)
    return target


def rename_world(server_root: Path, world: Path, backup_dir: Path, new_name: str) -> Path:
    if not world.exists() or world.suffix.lower() != ".sav":
        raise FileNotFoundError(world)
    clean = new_name.strip()
    if not clean:
        raise ValueError("World name is required.")
    if len(clean) > 16:
        raise ValueError("Public world names must be 16 characters or fewer.")
    if any(ch in clean for ch in "\r\n\t"):
        raise ValueError("World name cannot contain line breaks or tabs.")
    target = world.with_name(clean + ".sav")
    if target.exists() and target.resolve() != world.resolve():
        raise FileExistsError(target)
    create_backup(server_root, backup_dir, [world], "pre-world-rename")
    world.replace(target)
    os.utime(target, None)
    return target
