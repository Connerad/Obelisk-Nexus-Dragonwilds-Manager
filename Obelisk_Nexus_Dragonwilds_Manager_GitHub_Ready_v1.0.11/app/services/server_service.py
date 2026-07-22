from __future__ import annotations

import os
import shlex
import shutil
import signal
import subprocess
import time
import ctypes
import urllib.request
import zipfile
from ctypes import wintypes
from pathlib import Path

from ..models import ServerProfile
from .config_service import ensure_dedicated_config
from .public_service import validate_public_profile


STEAMCMD_WINDOWS_URL = "https://steamcdn-a.akamaihd.net/client/installer/steamcmd.zip"


class ServerService:
    def __init__(self):
        self.processes: dict[str, subprocess.Popen] = {}
        self.log_handles: dict[str, object] = {}

    @staticmethod
    def find_executable(profile: ServerProfile) -> Path:
        for candidate in profile.executable_candidates:
            if candidate.exists() and candidate.is_file():
                return candidate
        raise FileNotFoundError("RSDragonwilds server executable was not found in the selected installation.")

    @staticmethod
    def apply_profile_config(profile: ServerProfile) -> None:
        ensure_dedicated_config(profile.config_file, {
            "OwnerId": profile.owner_id,
            "ServerName": profile.name,
            "DefaultWorldName": profile.world_name,
            "AdminPassword": profile.admin_password,
            "WorldPassword": profile.world_password,
        })

    @staticmethod
    def build_launch_args(profile: ServerProfile) -> list[str]:
        args = shlex.split(profile.launch_args, posix=os.name != "nt")
        lowered = [arg.casefold() for arg in args]
        if not any(arg.startswith("-port=") for arg in lowered):
            args.append(f"-Port={profile.port}")
        if not any(arg.startswith("-queryport=") for arg in lowered):
            args.append(f"-QueryPort={profile.query_port}")
        if not any(arg.startswith("-multihome=") for arg in lowered):
            args.append("-MultiHome=0.0.0.0")
        if not any("gamesession]:maxplayers=" in arg for arg in lowered):
            args.append(f"-ini:Game:[/Script/Engine.GameSession]:MaxPlayers={profile.max_players}")
        return args

    def start(self, profile: ServerProfile) -> int:
        validate_public_profile(profile)
        if self.is_running(profile):
            raise RuntimeError("This server is already running.")
        exe = self.find_executable(profile)
        self.apply_profile_config(profile)
        args = [str(exe), *self.build_launch_args(profile)]
        creationflags = 0
        kwargs: dict[str, object] = {}
        if os.name == "nt":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        log_dir = profile.root / "ManagerLogs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = (log_dir / "server-console.log").open("ab", buffering=0)
        try:
            process = subprocess.Popen(
                args,
                cwd=exe.parent,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
                **kwargs,
            )
        except Exception:
            log_file.close()
            raise
        self.processes[profile.id] = process
        self.log_handles[profile.id] = log_file
        profile.process_id = process.pid
        return process.pid

    def is_running(self, profile: ServerProfile) -> bool:
        process = self.processes.get(profile.id)
        if process is not None:
            if process.poll() is None:
                return True
            self.processes.pop(profile.id, None)
            handle = self.log_handles.pop(profile.id, None)
            if handle:
                try: handle.close()
                except Exception: pass
            profile.process_id = None
        pid = profile.process_id
        if not pid:
            return False
        try:
            if os.name == "nt":
                executable = self._windows_pid_executable(pid)
                if executable:
                    expected = {str(path.resolve()).casefold() for path in profile.executable_candidates if path.exists()}
                    if not expected or str(Path(executable).resolve()).casefold() in expected:
                        return True
            else:
                os.kill(pid, 0)
                return True
        except Exception:
            pass
        profile.process_id = None
        return False

    @staticmethod
    def _windows_pid_executable(pid: int) -> str | None:
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return None
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)) or exit_code.value != STILL_ACTIVE:
                return None
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return None
            return buffer.value
        finally:
            kernel32.CloseHandle(handle)

    def stop(self, profile: ServerProfile, timeout: float = 15.0, force: bool = False) -> None:
        process = self.processes.get(profile.id)
        pid = profile.process_id
        if not pid and not process:
            return
        if process and process.poll() is None:
            if os.name == "nt":
                if force:
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], timeout=10, check=False, capture_output=True)
                else:
                    process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
                except ProcessLookupError:
                    pass
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], timeout=10, check=False, capture_output=True)
                else:
                    try: os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired: pass
            self.processes.pop(profile.id, None)
            handle = self.log_handles.pop(profile.id, None)
            if handle:
                try: handle.close()
                except Exception: pass
        elif pid:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(pid), "/T", *( ["/F"] if force else [] )], timeout=10, check=False)
            else:
                os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
        profile.process_id = None

    def restart(self, profile: ServerProfile) -> int:
        self.stop(profile)
        time.sleep(0.5)
        return self.start(profile)

    @staticmethod
    def _extract_steamcmd_zip(archive: Path, target: Path) -> Path:
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive, "r") as zf:
            if len(zf.infolist()) > 2000:
                raise RuntimeError("SteamCMD archive contains an unexpected number of files.")
            for info in zf.infolist():
                normalized = info.filename.replace("\\", "/")
                member = Path(normalized)
                if member.is_absolute() or ".." in member.parts or normalized.startswith("/"):
                    raise RuntimeError(f"Unsafe SteamCMD archive path: {info.filename}")
            bad = zf.testzip()
            if bad:
                raise RuntimeError(f"SteamCMD archive is damaged at {bad}.")
            zf.extractall(target)
        executable = target / "steamcmd.exe"
        if not executable.is_file():
            raise RuntimeError("SteamCMD archive did not contain steamcmd.exe.")
        return executable

    @classmethod
    def ensure_steamcmd(cls, tool_dir: Path) -> Path:
        if os.name != "nt":
            existing = shutil.which("steamcmd") or shutil.which("steamcmd.sh")
            if existing:
                return Path(existing)
            raise RuntimeError("Automatic SteamCMD setup is currently provided for Windows builds.")
        target = tool_dir / "steamcmd"
        executable = target / "steamcmd.exe"
        if executable.is_file():
            return executable
        tool_dir.mkdir(parents=True, exist_ok=True)
        archive = tool_dir / "steamcmd.zip"
        partial = archive.with_suffix(".zip.part")
        request = urllib.request.Request(STEAMCMD_WINDOWS_URL, headers={"User-Agent": "DragonwildsServerManagerRebuild/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=120) as response, partial.open("wb") as output:
                total = 0
                while True:
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    output.write(chunk); total += len(chunk)
                    if total > 100 * 1024 * 1024:
                        raise RuntimeError("SteamCMD download exceeded the expected size limit.")
            if partial.stat().st_size < 500_000:
                raise RuntimeError("SteamCMD download was incomplete.")
            partial.replace(archive)
            return cls._extract_steamcmd_zip(archive, target)
        finally:
            partial.unlink(missing_ok=True)

    @staticmethod
    def run_steamcmd(steamcmd: Path, install_dir: Path, validate: bool = False) -> subprocess.CompletedProcess:
        install_dir.mkdir(parents=True, exist_ok=True)
        args = [str(steamcmd), "+force_install_dir", str(install_dir), "+login", "anonymous", "+app_update", "4019830"]
        if validate:
            args.append("validate")
        args.append("+quit")
        return subprocess.run(args, capture_output=True, text=True, timeout=3600, check=False)
