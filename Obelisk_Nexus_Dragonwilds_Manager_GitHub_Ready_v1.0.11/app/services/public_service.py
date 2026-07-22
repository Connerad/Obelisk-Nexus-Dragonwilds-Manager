from __future__ import annotations

import ctypes
import os
import re
from dataclasses import dataclass
from pathlib import Path

from ..models import ServerProfile
from .config_service import DEDICATED_SECTION, IniDocument, ensure_dedicated_config
from .world_service import list_worlds

MAX_PUBLIC_WORLD_NAME = 16
MANDATORY_KEYS = ("OwnerId", "ServerName", "DefaultWorldName", "AdminPassword")


@dataclass(slots=True)
class ReadinessCheck:
    label: str
    ok: bool
    detail: str


def _clean_text(value: str) -> str:
    return str(value or "").strip()


def validate_world_name(name: str) -> str:
    value = _clean_text(name)
    if not value:
        raise ValueError("World name is required for the public server list.")
    if len(value) > MAX_PUBLIC_WORLD_NAME:
        raise ValueError(
            f"World names used by the public list must be {MAX_PUBLIC_WORLD_NAME} characters or fewer. "
            f"'{value}' is {len(value)} characters."
        )
    if any(ch in value for ch in "\r\n\t"):
        raise ValueError("World name cannot contain line breaks or tabs.")
    return value


def validate_public_profile(profile: ServerProfile) -> None:
    errors: list[str] = []
    if not _clean_text(profile.owner_id):
        errors.append("Owner Player ID is required.")
    if not _clean_text(profile.name):
        errors.append("Server name is required.")
    try:
        validate_world_name(profile.world_name)
    except ValueError as exc:
        errors.append(str(exc))
    if not str(profile.admin_password or ""):
        errors.append("Admin password is required by the dedicated server.")
    for label, value in (("Server name", profile.name), ("Owner Player ID", profile.owner_id)):
        if any(ch in str(value) for ch in "\r\n\t"):
            errors.append(f"{label} cannot contain line breaks or tabs.")
    if errors:
        raise ValueError("Public server setup is incomplete:\n\n" + "\n".join(f"• {e}" for e in errors))


def latest_world(profile: ServerProfile) -> Path | None:
    worlds = list_worlds(profile.save_dir)
    return worlds[0] if worlds else None


def public_search_name(profile: ServerProfile) -> str:
    world = latest_world(profile)
    return world.stem if world else _clean_text(profile.world_name)


def _config_values(profile: ServerProfile) -> dict[str, str]:
    if not profile.config_file.exists():
        return {}
    doc = IniDocument.load(profile.config_file)
    return {
        entry.key: entry.value
        for entry in doc.entries()
        if entry.section.casefold() == DEDICATED_SECTION.casefold()
    }


def repair_public_config(profile: ServerProfile) -> str:
    validate_public_profile(profile)
    ensure_dedicated_config(
        profile.config_file,
        {
            "OwnerId": _clean_text(profile.owner_id),
            "ServerName": _clean_text(profile.name),
            "DefaultWorldName": validate_world_name(profile.world_name),
            "AdminPassword": str(profile.admin_password),
            "WorldPassword": str(profile.world_password or ""),
        },
    )
    return public_search_name(profile)


def build_readiness_report(profile: ServerProfile) -> tuple[list[ReadinessCheck], str]:
    checks: list[ReadinessCheck] = []
    exact_name = public_search_name(profile)
    world = latest_world(profile)

    checks.append(ReadinessCheck("Server executable", any(p.is_file() for p in profile.executable_candidates),
                                 "Dedicated server files found." if any(p.is_file() for p in profile.executable_candidates)
                                 else "Install or validate the dedicated server through SteamCMD."))
    checks.append(ReadinessCheck("Owner Player ID", bool(_clean_text(profile.owner_id)),
                                 "Owner ID is set." if _clean_text(profile.owner_id) else "Owner ID is missing; the server cannot register."))
    checks.append(ReadinessCheck("Admin password", bool(str(profile.admin_password or "")),
                                 "Admin password is set." if str(profile.admin_password or "") else "Admin password is missing."))
    checks.append(ReadinessCheck("Server name", bool(_clean_text(profile.name)),
                                 _clean_text(profile.name) or "Server name is missing."))

    name_ok = bool(exact_name) and len(exact_name) <= MAX_PUBLIC_WORLD_NAME
    source = f"newest save file: {world.name}" if world else "DefaultWorldName (no save exists yet)"
    checks.append(ReadinessCheck("Exact public search name", name_ok,
                                 f"Search exactly: {exact_name!r} ({source}, case-sensitive)."
                                 if name_ok else f"{exact_name!r} is invalid or exceeds {MAX_PUBLIC_WORLD_NAME} characters."))

    values = _config_values(profile)
    expected = {
        "OwnerId": _clean_text(profile.owner_id),
        "ServerName": _clean_text(profile.name),
        "DefaultWorldName": _clean_text(profile.world_name),
        "AdminPassword": str(profile.admin_password or ""),
        "WorldPassword": str(profile.world_password or ""),
    }
    config_ok = profile.config_file.exists() and all(values.get(k, "") == v for k, v in expected.items())
    config_detail = str(profile.config_file)
    if not config_ok:
        missing = [k for k, v in expected.items() if values.get(k, "") != v]
        config_detail = "Config needs repair: " + ", ".join(missing or ["file missing"])
    checks.append(ReadinessCheck("DedicatedServer.ini", config_ok, config_detail))

    checks.append(ReadinessCheck("Game UDP port", 1 <= int(profile.port) <= 65535,
                                 f"UDP {profile.port} must be allowed by Windows Firewall and forwarded by the router."))
    checks.append(ReadinessCheck("Query UDP port", 1 <= int(profile.query_port) <= 65535,
                                 f"UDP {profile.query_port} is used for server discovery/query traffic and should be allowed."))

    if profile.log_file.exists():
        try:
            tail = profile.log_file.read_text(encoding="utf-8", errors="replace")[-250_000:]
        except OSError as exc:
            checks.append(ReadinessCheck("Server log", False, f"Could not read log: {exc}"))
        else:
            lower = tail.casefold()
            fatal_markers = ("owner id", "failed to bind", "address already in use", "fatal error", "eos error")
            found = [marker for marker in fatal_markers if marker in lower]
            # Do not mark every occurrence of 'owner id' as fatal; only flag explicit missing/invalid patterns.
            explicit_errors = [
                phrase for phrase in (
                    "owner id is required", "ownerid is required", "invalid owner", "failed to bind",
                    "address already in use", "fatal error", "eos_invalid", "eos error"
                ) if phrase in lower
            ]
            checks.append(ReadinessCheck("Server log", not explicit_errors,
                                         "No obvious public-list startup error was found in the recent log."
                                         if not explicit_errors else "Recent log contains: " + ", ".join(explicit_errors)))
    else:
        checks.append(ReadinessCheck("Server log", False, "Start the server once, then check again so registration/version errors can be read."))

    return checks, exact_name


def format_readiness_report(profile: ServerProfile) -> str:
    checks, exact_name = build_readiness_report(profile)
    lines = [
        f"PUBLIC SERVER READINESS — {profile.name}",
        "",
        f"SEARCH THIS EXACT WORLD NAME: {exact_name}",
        "The in-game search is case-sensitive.",
        "",
    ]
    for check in checks:
        lines.append(f"{'PASS' if check.ok else 'FIX'}  {check.label}: {check.detail}")
    lines += [
        "",
        "Dragonwilds public-list requirements:",
        "• Server and game client must be on the same build.",
        "• Search the current .sav filename without .sav, not the profile/server name.",
        "• UDP game/query ports must be allowed locally; router forwarding is required for outside players.",
        "• Restart the server after changing DedicatedServer.ini.",
    ]
    return "\n".join(lines)


def request_windows_firewall_rules(profile: ServerProfile, tools_dir: Path) -> Path:
    if os.name != "nt":
        raise RuntimeError("Automatic Windows Firewall setup is available only on Windows.")
    tools_dir.mkdir(parents=True, exist_ok=True)
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "", profile.id)[:16]
    script = tools_dir / f"open-dragonwilds-ports-{safe_id}.cmd"
    game_rule = f"Dragonwilds {safe_id} Game UDP {profile.port}"
    query_rule = f"Dragonwilds {safe_id} Query UDP {profile.query_port}"
    script.write_text(
        "@echo off\r\n"
        "title Dragonwilds Server Manager - Windows Firewall\r\n"
        f'netsh advfirewall firewall delete rule name="{game_rule}" >nul 2>&1\r\n'
        f'netsh advfirewall firewall add rule name="{game_rule}" dir=in action=allow protocol=UDP localport={profile.port} profile=any enable=yes\r\n'
        f'netsh advfirewall firewall delete rule name="{query_rule}" >nul 2>&1\r\n'
        f'netsh advfirewall firewall add rule name="{query_rule}" dir=in action=allow protocol=UDP localport={profile.query_port} profile=any enable=yes\r\n'
        "echo.\r\necho Firewall rules were requested. Router port forwarding is still required for players outside your network.\r\npause\r\n",
        encoding="utf-8",
    )
    result = ctypes.windll.shell32.ShellExecuteW(None, "runas", "cmd.exe", f'/c "{script}"', str(tools_dir), 1)
    if result <= 32:
        raise RuntimeError(f"Windows could not open the elevated firewall setup (code {result}).")
    return script
