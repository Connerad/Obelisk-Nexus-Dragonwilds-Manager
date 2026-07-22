from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

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
    server_root = base / "server"
    profile = ServerProfile(name="Stress", install_dir=str(server_root), port=7777, query_port=27015)
    state = {"failed": None, "start": None, "end": None, "jobs": 0}
    pages = ["Dashboard", "Servers", "Configuration", "Worlds", "Backups", "Mods", "Discord", "Logs", "Settings"]
    mods = [ModRecord(mod_id=i, name=f"Stress Mod {i}", summary="Stress rendering", downloads=i) for i in range(1, 21)]

    def fail(exc):
        state["failed"] = exc
        root.after(0, root.destroy)

    def begin():
        try:
            app.profiles = [profile]
            app.selected_id = profile.id
            app._refresh_profile_selector()
            state["start"] = app._heartbeat
            cycle(0)
            for _ in range(50):
                app.runner.submit(time.sleep, 0.03, on_success=lambda _value: state.update(jobs=state["jobs"] + 1))
        except Exception as exc:
            fail(exc)

    def cycle(index):
        try:
            if index >= 180:
                wait_jobs()
                return
            page = pages[index % len(pages)]
            app.show_page(page)
            if page == "Mods":
                app._mods_loaded((mods, len(mods), {}))
            root.after(12, lambda: cycle(index + 1))
        except Exception as exc:
            fail(exc)

    def wait_jobs():
        if state["jobs"] >= 50:
            state["end"] = app._heartbeat
            root.after(100, root.destroy)
        else:
            root.after(25, wait_jobs)

    root.after(250, begin)
    root.after(12000, lambda: fail(TimeoutError("UI stress timeout")))
    root.mainloop()
    app.runner.close(wait=True)
    temp.cleanup()
    if state["failed"]:
        raise state["failed"]
    ticks = state["end"] - state["start"]
    if ticks < 20 or state["jobs"] != 50:
        raise AssertionError(f"UI stress failed: {state}")
    print(f"UI_STRESS_PASS transitions=180 jobs={state['jobs']} heartbeat={ticks}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
