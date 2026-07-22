from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path


def _report_startup_failure(exc: BaseException) -> None:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "DragonwildsServerManagerRebuild"
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    report = log_dir / "startup-crash.log"
    report.write_text("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)), encoding="utf-8")
    message = f"The manager could not start. Details were written to:\n{report}\n\n{exc}"
    try:
        import tkinter as tk
        from tkinter import messagebox
        window = tk.Tk()
        window.withdraw()
        messagebox.showerror("Dragonwilds Server Manager", message, parent=window)
        window.destroy()
    except Exception:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "Dragonwilds Server Manager", 0x10)
        except Exception:
            pass


try:
    from app.main import main
    raise SystemExit(main())
except SystemExit:
    raise
except BaseException as error:
    _report_startup_failure(error)
    raise SystemExit(1)
