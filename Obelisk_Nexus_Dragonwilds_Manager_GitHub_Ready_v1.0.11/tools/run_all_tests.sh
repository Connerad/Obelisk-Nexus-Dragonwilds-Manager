#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

python3 -m compileall -q app tests
python3 -m unittest discover -s tests -p 'test_*.py' -v

(
  cd launcher
  GOOS=windows GOARCH=amd64 CGO_ENABLED=0 go vet ./...
  GOOS=windows GOARCH=amd64 CGO_ENABLED=0 go build -trimpath -ldflags='-s -w -H=windowsgui' -o ../DragonwildsServerManager.exe .
)

python3 tools/build_installer_payload.py

(
  cd installer
  GOOS=windows GOARCH=amd64 CGO_ENABLED=0 go vet ./...
  GOOS=windows GOARCH=amd64 CGO_ENABLED=0 go build -trimpath -ldflags='-s -w -H=windowsgui' -o ../Dragonwilds_Server_Manager_Setup_v1.0.11.exe .
)

if [[ "${RUN_DESKTOP_UI_TESTS:-0}" == "1" ]]; then
  command -v xvfb-run >/dev/null || { echo "xvfb-run is required for desktop UI tests" >&2; exit 1; }
  run_ui() {
    local test_file="$1"
    echo "==> Desktop UI test: $test_file"
    timeout -k 5s 180s xvfb-run -a -s '-screen 0 1280x1024x24 -nolisten tcp' \
      env PYTHONPATH=. python3 -u "$test_file"
    sleep 1
  }
  run_ui tests/test_server_editor_ui.py
  run_ui tests/ui_nexus_catalog.py
  run_ui tests/ui_public_listing.py
  run_ui tests/ui_smoke.py
  run_ui tests/ui_workflow_smoke.py
  run_ui tests/ui_stress.py
else
  echo "Core/build suite passed. Set RUN_DESKTOP_UI_TESTS=1 to run the virtual-desktop suite."
fi
