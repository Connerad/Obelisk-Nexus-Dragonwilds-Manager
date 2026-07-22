# Obelisk Nexus Dragonwilds Manager

Source repository for the Windows desktop **Dragonwilds Server Manager v1.0.11 Nexus Submission Candidate**, prepared for Nexus Mods source review.

> Unofficial community software. This project is not affiliated with, endorsed by, or sponsored by Jagex, RuneScape, Nexus Mods, or any mod author.

## What this repository contains

- Complete editable Python/Tk application source
- Go launcher source
- Go installer source
- Automated tests
- Build scripts and reproducible build instructions
- Nexus API usage and data-handling documentation
- Application assets required by the source build

Compiled `.exe` files and the generated installer `payload.zip` are intentionally excluded from source control. They are reproducible from the files in this repository.

## Nexus Mods integration

The submitted v1.0.11 review candidate uses the official Nexus Mods API and a locally entered Personal API key for the existing reviewer/testing flow. It does not scrape Nexus Mods pages or bypass Nexus download restrictions.

Nexus Support has advised that the production desktop integration can use OAuth. The planned OAuth migration is documented in `docs/NEXUS_OAUTH_MIGRATION_PLAN.md`; it is not falsely represented as already implemented in this source snapshot.

## Build and test

See [`BUILD.md`](BUILD.md) for reproducible Windows build instructions.

Core tests:

```bash
python -m unittest discover -s tests -p "test_*.py" -v
```

The current uploaded source snapshot passes all 48 core/Nexus unit tests.

## Security

Do not commit API keys, OAuth tokens, server passwords, Discord tokens/webhooks, private signing keys, or local runtime data. See [`SECURITY.md`](SECURITY.md).

## Nexus reviewer documents

- `NEXUS_REVIEW_README.md`
- `API_USAGE_MAP.md`
- `DATA_HANDLING.md`
- `docs/NEXUS_OAUTH_MIGRATION_PLAN.md`
- `SOURCE_MANIFEST.sha256`
