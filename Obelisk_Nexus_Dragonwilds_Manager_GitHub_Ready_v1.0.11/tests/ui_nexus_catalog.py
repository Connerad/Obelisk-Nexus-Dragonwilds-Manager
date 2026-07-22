from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tkinter as tk

from app.models import ModRecord
from app.ui import DragonwildsManagerApp


def pump(root: tk.Tk, seconds: float = 0.4) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        root.update()
        time.sleep(0.01)


def run() -> int:
    with tempfile.TemporaryDirectory() as td:
        os.environ["XDG_DATA_HOME"] = str(Path(td) / "data")
        root = tk.Tk()
        app = DragonwildsManagerApp(root)
        pump(root, 0.25)
        app.show_page("Mods")
        pump(root, 0.2)

        app._mods_loaded(([], 0, {}, "Nexus REST API fallback", "no matching records"))
        pump(root, 0.1)
        assert app.mod_cards.winfo_children(), "Empty catalog explanation card was not rendered"
        assert "Nexus REST API fallback" in app.mod_page_label.cget("text"), "Catalog source is not visible"

        app._mods_failed(RuntimeError("HTTP 403: Nexus security page"))
        pump(root, 0.1)
        error_text = " ".join(
            str(widget.cget("text"))
            for card in app.mod_cards.winfo_children()
            for widget in card.winfo_children()
            if isinstance(widget, tk.Label)
        )
        assert "catalog request failed" in error_text.lower(), "Catalog failure explanation was not rendered"
        assert any(
            isinstance(widget, tk.Button) and widget.cget("text") == "Retry catalog"
            for card in app.mod_cards.winfo_children()
            for frame in card.winfo_children()
            if isinstance(frame, tk.Frame)
            for widget in frame.winfo_children()
        ), "Catalog error card has no retry action"

        old = [ModRecord(mod_id=i, name=f"Old {i}") for i in range(1, 41)]
        new = [ModRecord(mod_id=900 + i, name=f"Newest {i}") for i in range(1, 5)]
        app._mods_loaded((old, len(old), {}, "Nexus GraphQL API", "current schema"))
        app._mods_loaded((new, len(new), {}, "Nexus GraphQL API", "current schema"))
        pump(root, 0.4)
        visible_text = " ".join(
            str(widget.cget("text"))
            for card in app.mod_cards.winfo_children()
            for widget in card.winfo_children()
            if isinstance(widget, tk.Frame)
            for widget in widget.winfo_children()
            if isinstance(widget, tk.Label)
        )
        assert "Newest 1" in visible_text, "Newest catalog page did not render"
        assert "Old 40" not in visible_text, "Stale catalog renderer continued after a newer result"

        app.runner.close(wait=True)
        root.destroy()
        print("UI_NEXUS_CATALOG_PASS empty-state, source label, stale-render cancellation")
        return 0


if __name__ == "__main__":
    raise SystemExit(run())
