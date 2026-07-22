from __future__ import annotations

import os
import tempfile
import time
import tkinter as tk
from pathlib import Path


def walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from walk(child)


def pump(root: tk.Tk, seconds: float = 0.2) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        root.update()
        time.sleep(0.01)


def find_button(dialog: tk.Toplevel, text: str) -> tk.Button:
    button = next(
        (w for w in walk(dialog) if isinstance(w, tk.Button) and w.cget("text") == text),
        None,
    )
    assert button is not None, f"{text} action is missing"
    return button


def find_grid_entry(dialog: tk.Toplevel, row: int) -> tk.Entry:
    for widget in walk(dialog):
        if isinstance(widget, tk.Entry):
            info = widget.grid_info()
            if str(info.get("row")) == str(row):
                return widget
    raise AssertionError(f"Entry at grid row {row} was not found")


def wait_until(root: tk.Tk, predicate, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root.update()
        if predicate():
            return True
        time.sleep(0.02)
    return False


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        os.environ["XDG_DATA_HOME"] = str(Path(td) / "data")
        import app.ui as ui_module
        from app.models import ServerProfile
        from app.ui import DragonwildsManagerApp

        # Never block the automated UI test on a modal error dialog.
        recorded_errors: list[str] = []
        ui_module.messagebox.showerror = lambda _title, message, **_kwargs: recorded_errors.append(str(message))

        root = tk.Tk()
        root.geometry("900x600+0+0")
        root.tk.call("tk", "scaling", 1.5)
        app = DragonwildsManagerApp(root)
        pump(root, 0.5)
        app.show_page("Servers")

        # 1. The create/save action is always visible on a short, scaled screen.
        app.open_server_editor()
        pump(root, 0.3)
        dialogs = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
        assert dialogs, "Create-server dialog did not open"
        dialog = dialogs[-1]
        create = find_button(dialog, "Create & Save Server")
        assert create.winfo_ismapped(), "Create & Save Server action is not mapped"

        # The selector must actually change the form instead of storing a universal label.
        type_combo = next(
            w for w in walk(dialog)
            if isinstance(w, __import__("tkinter.ttk", fromlist=["Combobox"]).Combobox)
            and "Creative" in tuple(w.cget("values"))
        )
        source_label = next(w for w in walk(dialog) if isinstance(w, tk.Label) and w.cget("text") == "Source world save")
        world_label = next(w for w in walk(dialog) if isinstance(w, tk.Label) and "world name" in str(w.cget("text")).lower())
        type_combo.set("Creative"); type_combo.event_generate("<<ComboboxSelected>>"); pump(root, 0.1)
        assert source_label.grid_info(), "Creative mode did not reveal the required .sav selector"
        assert "Public world name" in world_label.cget("text"), "Creative mode did not change the world-name behavior"
        type_combo.set("Custom"); type_combo.event_generate("<<ComboboxSelected>>"); pump(root, 0.1)
        assert source_label.grid_info(), "Custom mode did not keep the required .sav selector visible"
        type_combo.set("Campaign / Standard"); type_combo.event_generate("<<ComboboxSelected>>"); pump(root, 0.1)
        assert not source_label.grid_info(), "Campaign mode incorrectly kept the imported .sav selector visible"
        screen_h = dialog.winfo_screenheight()
        bottom = create.winfo_rooty() + create.winfo_height()
        assert bottom <= screen_h, f"Create & Save Server action is clipped below the screen ({bottom}>{screen_h})"

        install_entry = find_grid_entry(dialog, 3)
        server_root = Path(td) / "server-one"
        install_entry.delete(0, "end")
        install_entry.insert(0, str(server_root))
        owner_entry = find_grid_entry(dialog, 5)
        owner_entry.delete(0, "end")
        owner_entry.insert(0, "owner-test-id")
        admin_entry = find_grid_entry(dialog, 6)
        admin_entry.delete(0, "end")
        admin_entry.insert(0, "admin-test-password")
        create.invoke()
        assert wait_until(root, lambda: len(app.profiles) == 1), "Create & Save Server did not persist a profile"
        assert app.profiles[0].install_dir == str(server_root)
        assert app.profiles[0].config_file.exists(), "DedicatedServer.ini was not created"
        assert not dialog.winfo_exists(), "Dialog did not close after a successful save"

        # 2. A failed save keeps the editor open and re-enables the action.
        # Force a duplicate game port to trigger transactional validation failure.
        app.open_server_editor()
        pump(root, 0.3)
        dialog = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][-1]
        create = find_button(dialog, "Create & Save Server")
        install_entry = find_grid_entry(dialog, 3)
        install_entry.delete(0, "end")
        install_entry.insert(0, str(Path(td) / "server-two"))
        owner_entry = find_grid_entry(dialog, 5)
        owner_entry.delete(0, "end")
        owner_entry.insert(0, "owner-test-id-2")
        admin_entry = find_grid_entry(dialog, 6)
        admin_entry.delete(0, "end")
        admin_entry.insert(0, "admin-test-password")
        port_entry = find_grid_entry(dialog, 9)
        port_entry.delete(0, "end")
        port_entry.insert(0, str(app.profiles[0].port))
        create.invoke()
        assert wait_until(root, lambda: bool(recorded_errors)), "Expected duplicate-port validation failure"
        assert dialog.winfo_exists(), "Editor closed after a failed save"
        assert str(create.cget("state")) == "normal", "Create & Save Server was not re-enabled after failure"
        assert len(app.profiles) == 1, "Failed create unexpectedly added a profile"
        dialog.destroy()

        # 3. Custom mode must require and copy the chosen save instead of acting universal.
        custom_source = Path(td) / "custom-source.sav"
        custom_source.write_bytes(b"custom-save")
        app.open_server_editor()
        pump(root, 0.3)
        dialog = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][-1]
        create = find_button(dialog, "Create & Save Server")
        type_combo = next(
            w for w in walk(dialog)
            if isinstance(w, __import__("tkinter.ttk", fromlist=["Combobox"]).Combobox)
            and "Custom" in tuple(w.cget("values"))
        )
        type_combo.set("Custom"); type_combo.event_generate("<<ComboboxSelected>>"); pump(root, 0.1)
        custom_root = Path(td) / "server-custom"
        values = {
            3: str(custom_root),
            4: "MyCustomWorld",
            5: "owner-custom",
            6: "admin-custom",
            9: str(app.profiles[0].port + 1),
            10: str(app.profiles[0].query_port + 1),
            13: str(custom_source),
        }
        for grid_row, value in values.items():
            entry = find_grid_entry(dialog, grid_row)
            entry.delete(0, "end"); entry.insert(0, value)
        create.invoke()
        assert wait_until(root, lambda: len(app.profiles) == 2), "Custom server was not persisted"
        custom_profile = app.profiles[-1]
        assert custom_profile.server_type == "Custom"
        assert (custom_profile.save_dir / "MyCustomWorld.sav").read_bytes() == b"custom-save"

        app.runner.close(wait=False)
        root.destroy()
        print("SERVER_EDITOR_UI_PASS")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
