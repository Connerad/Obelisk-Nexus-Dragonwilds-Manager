# Reproducible Build Notes

## Source version

`1.0.11 Nexus Submission Candidate`

## Requirements

- Python 3.12+ with Tkinter
- Go 1.22+
- A shell capable of running the commands below (Git Bash, WSL, or equivalent for the supplied `.sh` scripts)

The application itself uses the Python standard library and Tkinter and does not install third-party Python packages at runtime.

## 1. Run the source tests

```bash
python -m compileall -q app tests
python -m unittest discover -s tests -p "test_*.py" -v
```

## 2. Build the Windows launcher

```bash
cd launcher
GOOS=windows GOARCH=amd64 CGO_ENABLED=0 go build -trimpath -ldflags="-H windowsgui -s -w" -o ../DragonwildsServerManager.exe .
cd ..
```

## 3. Generate the installer payload

After the launcher exists at the repository root:

```bash
python tools/build_installer_payload.py
```

This generates `installer/payload.zip` from the exact runtime files required by the installer. The generated ZIP is intentionally ignored by Git.

## 4. Build the Windows installer

```bash
cd installer
GOOS=windows GOARCH=amd64 CGO_ENABLED=0 go build -trimpath -ldflags="-H windowsgui -s -w" -o ../Dragonwilds_Server_Manager_Setup_v1.0.11.exe .
cd ..
```

## Private runtime behavior

The launcher provisions/uses an application-private Python runtime. When `pythonw.exe` is unavailable, it can use the private `python.exe` interpreter with the console hidden.

## Source integrity

`SOURCE_MANIFEST.sha256` contains SHA-256 hashes for the source-review files in this repository. Generated binaries are excluded.
