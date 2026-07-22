# Nexus OAuth Migration Plan

This document describes the planned production authentication update requested after Nexus Support confirmed that OAuth is available for desktop mod-manager integrations.

## Current source snapshot

The v1.0.11 review candidate currently implements a Personal API-key testing/reviewer workflow. OAuth is **not** claimed to be implemented in this snapshot.

## Planned installed-application flow

1. Treat the Windows desktop application as a public/installed client.
2. Generate a cryptographically random PKCE verifier and challenge for each authorization attempt.
3. Open the Nexus authorization page in the user's default browser.
4. Listen only on the registered loopback callback: `http://127.0.0.1:1337/callback`.
5. Validate state and exchange the returned authorization code using the PKCE verifier.
6. Store returned tokens locally using the application's secure local secret-storage layer.
7. Refresh or revoke credentials only through supported Nexus endpoints.
8. Never embed a confidential client secret in the distributed desktop application.

## Download behavior

- Premium users: use authorized API download behavior when permitted by Nexus Mods.
- Free users: follow the Nexus-required manual download/page redirect flow.
- The manager may install a file only after the user has obtained it through an allowed flow.
- No scraping or account-tier bypass will be introduced.

## Requested capabilities/scopes

The application needs only the permissions required for account authentication, mod/file metadata, and authorized downloads. Final scope identifiers will follow the values assigned or recommended by Nexus during registration.
