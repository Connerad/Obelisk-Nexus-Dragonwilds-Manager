from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tkinter as tk

from app.models import ModRecord, ServerProfile
from app.storage import ProfileStore
from app.ui import DragonwildsManagerApp


def run() -> int:
    temp = tempfile.TemporaryDirectory()
    base = Path(temp.name)
    os.environ["XDG_DATA_HOME"] = str(base / "data")

    root = tk.Tk()
    app = DragonwildsManagerApp(root)
    app.store = ProfileStore(base / "profiles.json")
    server = base / "server"
    cfg = server / "RSDragonwilds" / "Saved" / "Config" / "WindowsServer" / "DedicatedServer.ini"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        "[/Script/Dominion.DedicatedServerSettings]\nServerName=Smoke\nFutureKey=Keep\n",
        encoding="utf-8",
    )
    profile = ServerProfile(name="Smoke", install_dir=str(server), owner_id="owner")
    app.profiles = [profile]
    app.selected_id = profile.id
    app._refresh_profile_selector()

    state = {
        "connect_start": None,
        "connect_end": None,
        "save_start": None,
        "save_end": None,
        "failed": None,
    }

    def fake_validate(self):
        time.sleep(0.7)
        return {"name": "Workflow Tester"}

    def fake_catalog(self, offset=0, count=50, search="", sort="downloads"):
        time.sleep(0.7)
        self.last_catalog_source = "GraphQL"
        self.last_catalog_note = "mock current schema"
        records = [
            ModRecord(
                mod_id=i,
                name=f"Workflow Mod {i}",
                version="1.0",
                author="Tester",
                summary="Background Nexus workflow test",
                category="Gameplay",
                downloads=i * 100,
            )
            for i in range(1, 7)
        ]
        return records, len(records)

    def fake_manifests(_root):
        time.sleep(0.4)
        return {}

    patches = [
        mock.patch("app.ui.NexusClient.validate", fake_validate),
        mock.patch("app.ui.NexusClient.catalog_page", fake_catalog),
        mock.patch("app.ui.load_manifests", fake_manifests),
        mock.patch("app.ui.save_secret", lambda _name, _value: time.sleep(0.2)),
    ]
    for patcher in patches:
        patcher.start()

    def fail(exc):
        state["failed"] = exc
        root.after(0, root.destroy)

    def begin():
        try:
            app.profiles = [profile]
            app.selected_id = profile.id
            app._refresh_profile_selector()
            app.show_page("Mods")
            app.nexus_key_var.set("test-key")
            state["connect_start"] = app._heartbeat
            app.connect_nexus()
            poll_mods()
        except Exception as exc:
            fail(exc)

    def poll_mods():
        if app.mod_total == 6:
            state["connect_end"] = app._heartbeat
            begin_config_save()
            return
        root.after(50, poll_mods)

    def begin_config_save():
        try:
            app.show_page("Configuration")
            app.selected_config = cfg
            app._config_text_loaded(cfg, cfg.read_text(encoding="utf-8"), [])
            app.config_text.delete("1.0", "end")
            app.config_text.insert("1.0", "[/Script/Dominion.DedicatedServerSettings]\nServerName=Saved\nFutureKey=Keep\n")
            original = app.server_service.is_running

            def slow_not_running(_profile):
                time.sleep(0.8)
                return False

            app.server_service.is_running = slow_not_running
            state["save_start"] = app._heartbeat
            app.save_current_config()
            poll_config(original)
        except Exception as exc:
            fail(exc)

    def poll_config(original):
        try:
            if cfg.exists() and "ServerName=Saved" in cfg.read_text(encoding="utf-8"):
                state["save_end"] = app._heartbeat
                app.server_service.is_running = original
                root.after(100, root.destroy)
                return
            root.after(50, lambda: poll_config(original))
        except Exception as exc:
            fail(exc)

    root.after(300, begin)
    root.after(10000, lambda: fail(TimeoutError("workflow smoke timeout")))
    root.mainloop()
    app.runner.close(wait=True)
    for patcher in reversed(patches):
        patcher.stop()
    temp.cleanup()

    if state["failed"]:
        raise state["failed"]
    connect_ticks = state["connect_end"] - state["connect_start"]
    save_ticks = state["save_end"] - state["save_start"]
    if connect_ticks < 10:
        raise AssertionError(f"Nexus workflow stalled UI heartbeat: {state}")
    if save_ticks < 5:
        raise AssertionError(f"Config save workflow stalled UI heartbeat: {state}")
    print(f"UI_WORKFLOW_PASS Nexus={connect_ticks} ticks ConfigSave={save_ticks} ticks")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
