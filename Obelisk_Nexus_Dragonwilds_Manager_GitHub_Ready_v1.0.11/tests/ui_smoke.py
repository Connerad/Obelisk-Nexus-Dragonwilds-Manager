from __future__ import annotations

import tempfile
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import time
import tkinter as tk
from app.models import ServerProfile, ModRecord
from app.storage import ProfileStore
from app.ui import DragonwildsManagerApp


def run() -> int:
    temp = tempfile.TemporaryDirectory()
    root_dir = Path(temp.name)
    tk_root = tk.Tk()
    app = DragonwildsManagerApp(tk_root)
    app.store = ProfileStore(root_dir / "profiles.json")
    server = root_dir / "server"
    config = server / "RSDragonwilds" / "Saved" / "Config" / "WindowsServer"
    config.mkdir(parents=True)
    (config / "DedicatedServer.ini").write_text("[/Script/Dominion.DedicatedServerSettings]\nServerName=Smoke Test\n", encoding="utf-8")
    app.profiles = [ServerProfile(name="Smoke Test", install_dir=str(server), port=7777)]
    app.selected_id = app.profiles[0].id
    app._refresh_profile_selector()
    pages = ["Dashboard", "Servers", "Configuration", "Worlds", "Backups", "Mods", "Discord", "Logs", "Settings"]
    state = {"start": None, "end": None, "failed": None}

    def visit(i=0):
        try:
            if i < len(pages):
                app.show_page(pages[i])
                tk_root.after(80, lambda: visit(i + 1))
            else:
                app.show_page("Mods")
                fake_mods = [ModRecord(mod_id=n, name=f"Fake Mod {n}", version="1.0", author="Tester", summary="UI rendering smoke test", category="Gameplay", downloads=n * 10) for n in range(1, 101)]
                app._mods_loaded((fake_mods[:20], 100, {}, "GraphQL", "current schema"))
                state["start"] = app._heartbeat
                # Simulate slow Nexus/disk work. The heartbeat and incremental card renderer must continue.
                app.runner.submit(time.sleep, 1.2, on_success=finish)
        except Exception as exc:
            state["failed"] = exc
            tk_root.after(0, tk_root.destroy)

    def finish(_=None):
        state["end"] = app._heartbeat
        tk_root.after(100, tk_root.destroy)

    tk_root.after(100, visit)
    tk_root.after(6000, lambda: (state.update(failed=TimeoutError("UI smoke timeout")), tk_root.destroy()))
    tk_root.mainloop()
    app.runner.close(wait=True)
    temp.cleanup()
    if state["failed"]:
        raise state["failed"]
    if state["start"] is None or state["end"] is None or state["end"] - state["start"] < 6:
        raise AssertionError(f"UI heartbeat stalled: {state}")
    print(f"UI_SMOKE_PASS heartbeat advanced {state['end'] - state['start']} ticks while a worker slept")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
