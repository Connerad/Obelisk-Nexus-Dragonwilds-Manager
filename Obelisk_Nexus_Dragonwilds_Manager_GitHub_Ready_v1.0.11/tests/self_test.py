from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def run_self_test() -> int:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", str(root / "tests"), "-p", "test_*.py", "-v"], cwd=root)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(run_self_test())
