# Nexus Mods Source Review Notes

## Application

**Current source product name:** Dragonwilds Server Manager  
**Project/repository:** Obelisk Nexus Dragonwilds Manager  
**Version:** 1.0.11 Nexus Submission Candidate  
**Game:** RuneScape: Dragonwilds (`runescapedragonwilds`)

## Source review

This repository contains the editable Python and Go source for the submitted desktop application. Compiled executables and the generated installer payload are excluded because they can be rebuilt from the included source.

## Current Nexus authentication in this snapshot

The v1.0.11 submission candidate contains the existing Personal API-key testing/reviewer flow. On Windows, locally entered credentials are stored through the application's local secret-storage implementation. The application has no project-operated backend that receives Nexus credentials.

This repository does **not** claim that OAuth is already implemented. Nexus Support has advised that OAuth is the appropriate production integration; the intended migration is documented in `docs/NEXUS_OAUTH_MIGRATION_PLAN.md`.

## API behavior

- Official Nexus API calls only
- No Nexus website scraping
- No background API polling
- Local catalog caching with expiration
- Rate-limit response information handled when supplied
- API keys are not forwarded to signed CDN download URLs
- No free-account direct-download bypass

See `API_USAGE_MAP.md` and `tests/test_nexus.py` for implementation details and automated policy-oriented tests.

## Download behavior

The current review build only performs direct-download behavior when permitted by the Nexus API response. It does not bypass restrictions for free accounts. The planned OAuth implementation will follow the account-tier behavior required by Nexus Mods: approved API downloads for eligible Premium users and the Nexus-required manual-download flow for free users.

## Build verification

See `BUILD.md`. The uploaded source snapshot passed all 48 core/Nexus unit tests before this GitHub package was produced.
