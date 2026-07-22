from __future__ import annotations

import os
import tempfile
import time
import tkinter as tk
from pathlib import Path


def pump(root: tk.Tk, seconds: float = 0.5) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        root.update()
        time.sleep(0.01)


def walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from walk(child)


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        os.environ["XDG_DATA_HOME"] = str(Path(td) / "data")
        import app.ui as ui_module
        from app.models import ServerProfile
        from app.services.server_service import ServerService
        from app.ui import DragonwildsManagerApp

        ui_module.messagebox.showerror = lambda *_a, **_k: None
        ui_module.messagebox.showinfo = lambda *_a, **_k: None
        root = tk.Tk()
        app = DragonwildsManagerApp(root)
        pump(root, 0.3)

        server_root = Path(td) / "server"
        profile = ServerProfile(
            name="Listing Server", install_dir=str(server_root), world_name="DefaultWorld",
            owner_id="owner-id", admin_password="admin-password",
        )
        profile.save_dir.mkdir(parents=True, exist_ok=True)
        (profile.save_dir / "PublicWorld.sav").write_bytes(b"save")
        exe = server_root / "RSDragonwilds.exe"
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_bytes(b"fake")
        ServerService.apply_profile_config(profile)
        app.profiles = [profile]
        app.selected_id = profile.id
        app._refresh_profile_selector()
        app.show_page("Dashboard")
        pump(root, 0.5)
        assert "PublicWorld" in app.dashboard_details.cget("text")

        app.public_listing_check()
        pump(root, 0.8)
        dialogs = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
        assert dialogs, "Public listing report did not open"
        report_texts = [w for w in walk(dialogs[-1]) if isinstance(w, tk.Text)]
        assert report_texts and "SEARCH THIS EXACT WORLD NAME: PublicWorld" in report_texts[0].get("1.0", "end")

        app.runner.close(wait=True)
        root.destroy()
        print("UI_PUBLIC_LISTING_PASS")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
