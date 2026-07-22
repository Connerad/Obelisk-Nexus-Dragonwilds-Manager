from __future__ import annotations

import os
import shutil
from pathlib import Path

from ..models import ServerProfile, WORLD_TYPES
from ..storage import ProfileStore
from .server_service import ServerService
from .public_service import validate_public_profile, validate_world_name


STANDARD_WORLD_TYPE = "Campaign / Standard"


def world_type_requires_save(server_type: str) -> bool:
    return server_type in {"Creative", "Custom", "Imported / Other"}


def _existing_worlds(profile: ServerProfile) -> list[Path]:
    if not profile.save_dir.exists():
        return []
    return [path for path in profile.save_dir.rglob("*.sav") if path.is_file() and path.stat().st_size > 0]


def _validate_profile(
    existing: list[ServerProfile],
    profile: ServerProfile,
    *,
    initial_world_source: str | Path | None = None,
) -> None:
    validate_public_profile(profile)
    if profile.server_type not in WORLD_TYPES:
        raise ValueError("Choose a supported server/world type.")
    if not profile.install_dir.strip():
        raise ValueError("Choose an installation folder.")
    if not (1 <= int(profile.max_players) <= 6):
        raise ValueError("Maximum players must be between 1 and 6.")
    if not (1 <= int(profile.port) <= 65535):
        raise ValueError("Game port must be between 1 and 65535.")
    if not (1 <= int(profile.query_port) <= 65535):
        raise ValueError("Query port must be between 1 and 65535.")
    if profile.port == profile.query_port:
        raise ValueError("Game port and query port must be different.")
    if any(p.id != profile.id and p.port == profile.port for p in existing):
        raise ValueError(f"Game port {profile.port} is already assigned to another profile.")
    if any(p.id != profile.id and p.query_port == profile.query_port for p in existing):
        raise ValueError(f"Query port {profile.query_port} is already assigned to another profile.")
    root = profile.root.resolve()
    if any(
        p.id != profile.id and p.install_dir and Path(p.install_dir).resolve() == root
        for p in existing
    ):
        raise ValueError("Another profile already uses this installation folder.")

    if world_type_requires_save(profile.server_type):
        source = Path(initial_world_source).expanduser() if initial_world_source else None
        if source is not None and (not source.is_file() or source.suffix.casefold() != ".sav"):
            raise ValueError(f"{profile.server_type} requires a valid Dragonwilds .sav world file.")
        if source is None and not _existing_worlds(profile):
            raise ValueError(
                f"{profile.server_type} cannot be created as an empty universal world. "
                "Create that world type in Dragonwilds first, then choose its .sav file here."
            )


def _prepare_profile_root(profile: ServerProfile) -> tuple[bool, bytes | None]:
    root = profile.root
    created_root = False
    if not root.exists():
        root.mkdir(parents=True)
        created_root = True
    old_config = profile.config_file.read_bytes() if profile.config_file.exists() else None
    profile.config_dir.mkdir(parents=True, exist_ok=True)
    profile.save_dir.mkdir(parents=True, exist_ok=True)
    return created_root, old_config


def _install_initial_world(profile: ServerProfile, source_value: str | Path | None) -> Path | None:
    if not source_value:
        return None
    source = Path(source_value).expanduser().resolve()
    if not source.is_file() or source.suffix.casefold() != ".sav":
        raise ValueError("Choose a valid Dragonwilds .sav world file.")
    public_name = validate_world_name(profile.world_name)
    target = profile.save_dir / f"{public_name}.sav"
    if target.exists():
        raise FileExistsError(
            f"{target.name} already exists. Use the Worlds page to replace an existing save safely."
        )
    temp = target.with_suffix(".sav.creating")
    shutil.copy2(source, temp)
    if temp.stat().st_size <= 0:
        temp.unlink(missing_ok=True)
        raise ValueError("The selected world save is empty.")
    os.utime(temp, None)
    temp.replace(target)
    return target


def _rollback_profile_root(
    profile: ServerProfile,
    created_root: bool,
    old_config: bytes | None,
    created_world: Path | None = None,
) -> None:
    if created_world is not None:
        created_world.unlink(missing_ok=True)
    if old_config is not None:
        profile.config_file.parent.mkdir(parents=True, exist_ok=True)
        profile.config_file.write_bytes(old_config)
    elif profile.config_file.exists():
        profile.config_file.unlink(missing_ok=True)
    if created_root:
        shutil.rmtree(profile.root, ignore_errors=True)


def create_profile_transaction(
    store: ProfileStore,
    existing: list[ServerProfile],
    profile: ServerProfile,
    initial_world_source: str | Path | None = None,
) -> ServerProfile:
    _validate_profile(existing, profile, initial_world_source=initial_world_source)
    created_root = False
    old_config = None
    created_world: Path | None = None
    try:
        created_root, old_config = _prepare_profile_root(profile)
        ServerService.apply_profile_config(profile)
        created_world = _install_initial_world(profile, initial_world_source)
        ProfileStore.stamp(profile, new=True)
        store.save([*existing, profile])
        return profile
    except Exception:
        _rollback_profile_root(profile, created_root, old_config, created_world)
        raise


def update_profile_transaction(
    store: ProfileStore,
    profiles: list[ServerProfile],
    profile: ServerProfile,
    initial_world_source: str | Path | None = None,
) -> ServerProfile:
    """Commit a profile edit without mutating the live profile list first."""
    original = next((p for p in profiles if p.id == profile.id), None)
    if original is None:
        raise ValueError("The server profile being edited no longer exists.")
    # Existing Creative/Custom/Imported profiles are valid when they already
    # contain a save. Replacing that save remains a separate Worlds operation.
    _validate_profile(profiles, profile, initial_world_source=initial_world_source)
    created_root = False
    old_config = None
    created_world: Path | None = None
    try:
        created_root, old_config = _prepare_profile_root(profile)
        ServerService.apply_profile_config(profile)
        created_world = _install_initial_world(profile, initial_world_source)
        ProfileStore.stamp(profile)
        committed = [profile if p.id == profile.id else p for p in profiles]
        store.save(committed)
        return profile
    except Exception:
        _rollback_profile_root(profile, created_root, old_config, created_world)
        raise
