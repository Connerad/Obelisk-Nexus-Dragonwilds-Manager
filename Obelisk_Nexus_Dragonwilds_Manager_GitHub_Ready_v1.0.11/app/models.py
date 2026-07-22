from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
import uuid


WORLD_TYPES = ("Campaign / Standard", "Creative", "Custom", "Imported / Other")


@dataclass(slots=True)
class ServerProfile:
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    name: str = "New Dragonwilds Server"
    server_type: str = WORLD_TYPES[0]
    install_dir: str = ""
    world_name: str = "World"
    owner_id: str = ""
    admin_password: str = ""
    world_password: str = ""
    max_players: int = 6
    port: int = 7777
    query_port: int = 27015
    launch_args: str = "-log -NewConsole"
    backup_dir: str = ""
    discord_webhook: str = ""
    auto_restart: bool = False
    auto_update: bool = False
    process_id: int | None = None
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ServerProfile":
        valid = {f.name for f in cls.__dataclass_fields__.values()}
        cleaned = {k: v for k, v in data.items() if k in valid}
        profile = cls(**cleaned)
        if profile.server_type not in WORLD_TYPES:
            profile.server_type = "Imported / Other"
        profile.max_players = max(1, min(6, int(profile.max_players or 6)))
        profile.port = max(1, min(65535, int(profile.port or 7777)))
        profile.query_port = max(1, min(65535, int(profile.query_port or 27015)))
        return profile

    def to_dict(self, include_secrets: bool = True) -> dict[str, Any]:
        data = asdict(self)
        if not include_secrets:
            data["admin_password"] = ""
            data["world_password"] = ""
            data["discord_webhook"] = ""
            data["process_id"] = None
        return data

    @property
    def root(self) -> Path:
        return Path(self.install_dir).expanduser()

    @property
    def config_dir(self) -> Path:
        if Path(self.install_dir).name.lower() == "rsdragonwilds":
            return self.root / "Saved" / "Config" / "WindowsServer"
        return self.root / "RSDragonwilds" / "Saved" / "Config" / "WindowsServer"

    @property
    def config_file(self) -> Path:
        return self.config_dir / "DedicatedServer.ini"

    @property
    def save_dir(self) -> Path:
        if Path(self.install_dir).name.lower() == "rsdragonwilds":
            return self.root / "Saved" / "SaveGames"
        return self.root / "RSDragonwilds" / "Saved" / "SaveGames"


    @property
    def log_file(self) -> Path:
        if Path(self.install_dir).name.lower() == "rsdragonwilds":
            return self.root / "Saved" / "Logs" / "RSDragonwilds.log"
        return self.root / "RSDragonwilds" / "Saved" / "Logs" / "RSDragonwilds.log"

    @property
    def executable_candidates(self) -> list[Path]:
        root = self.root
        return [
            root / "RSDragonwilds.exe",
            root / "RSDragonwilds" / "Binaries" / "Win64" / "RSDragonwildsServer-Win64-Shipping.exe",
            root / "RSDragonwilds" / "Binaries" / "Win64" / "RSDragonwilds-Win64-Shipping.exe",
            root / "RSDragonwilds" / "Binaries" / "Linux" / "RSDragonwildsServer",
        ]


@dataclass(slots=True)
class ModRecord:
    mod_id: int
    name: str
    version: str = ""
    author: str = ""
    summary: str = ""
    category: str = ""
    picture_url: str = ""
    downloads: int = 0
    endorsements: int = 0
    updated_at: str = ""
    installed_version: str = ""
    installed: bool = False


@dataclass(slots=True)
class TaskResult:
    ok: bool
    message: str
    data: Any = None
