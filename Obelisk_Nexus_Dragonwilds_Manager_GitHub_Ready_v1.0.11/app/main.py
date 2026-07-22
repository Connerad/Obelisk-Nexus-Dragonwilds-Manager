from __future__ import annotations

import argparse
import ctypes
import os
import sys
import tkinter as tk
from pathlib import Path

from .ui import DragonwildsManagerApp


_INSTANCE_MUTEX = None


def _acquire_single_instance() -> bool:
    global _INSTANCE_MUTEX
    if os.name != "nt":
        return True
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, "Local\\DragonwildsServerManagerRebuild-1.0.11")
    if not handle:
        return True
    already_exists = kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS
    if already_exists:
        kernel32.CloseHandle(handle)
        return False
    _INSTANCE_MUTEX = handle
    return True


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--self-test", action="store_true")
    args, unknown = parser.parse_known_args()
    if args.self_test:
        from tests.self_test import run_self_test
        return run_self_test()

    if not _acquire_single_instance():
        return 0

    root = tk.Tk()
    app = DragonwildsManagerApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
