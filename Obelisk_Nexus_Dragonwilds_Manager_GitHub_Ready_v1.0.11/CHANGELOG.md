# Changelog

## 1.0.11 — Runtime Repair / Nexus Submission Candidate

- Fixed the private-runtime failure reproduced by the uploaded Windows installer log.
- Detects an existing compatible Python 3.12 installation before invoking the CPython bootstrapper.
- Verifies the existing interpreter has `tkinter`, TLS, and URL support, then copies it into the manager-owned private Runtime directory.
- Excludes user-installed `site-packages` and `__pycache__` directories when creating the private runtime.
- Re-checks and recovers from an existing Python installation if the CPython bootstrapper enters Modify mode and ignores the requested private `TargetDir`.
- Added `%LOCALAPPDATA%\DragonwildsServerManagerRebuild\runtime-bootstrap.log` for runtime-source selection and recovery diagnostics.
- Keeps `pythonw.exe` optional and safely falls back to `python.exe` with the console hidden.
- Preserves all Nexus submission hardening from 1.0.10: official API-only catalog access, single REST fallback, cache expiry, rate-limit tracking, redacted reviewer diagnostics, branded installer, desktop shortcut, privacy documentation, and submission metadata.

## 1.0.10 — Private Runtime Compatibility Hotfix

- Removed the hard requirement for `pythonw.exe`.
- Added verification fallback to `python.exe` with a hidden console.
- Added private-runtime installer logging.

## 1.0.9 — Nexus Submission Candidate

- Limited the Nexus REST catalog compatibility path to one `latest_updated` request.
- Added six-hour expiration for cached Nexus catalog responses.
- Added Nexus API rate-limit header capture and redacted reporting.
- Added quota information to the Nexus Mods status line when Nexus supplies it.
- Separated the normal application-registration request from the additional free-account in-application download-capability request.
- Added a public-facing privacy notice and expanded data-handling documentation.
- Added Nexus registration metadata and a 1024×1024 dark-background-safe application logo.
- Added a custom Windows desktop shortcut icon.
- Added reproducible build notes and submission checklist.
- Added a Windows installer that installs the manager, provisions its private runtime during setup, creates desktop and Start Menu shortcuts, and registers an uninstaller.
