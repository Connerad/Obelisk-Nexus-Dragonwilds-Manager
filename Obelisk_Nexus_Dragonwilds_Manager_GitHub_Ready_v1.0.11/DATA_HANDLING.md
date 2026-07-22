# Nexus Data Handling

## Credentials

During the registration testing stage, the user enters a Personal API key. On Windows the key is protected locally with Windows DPAPI. It is not stored on a Dragonwilds Server Manager server because the application has no authentication backend.

The key is excluded from exported profiles, diagnostic reports, and request audit logs.

## Nexus metadata

Nexus metadata is used only inside the local desktop application. The application does not scrape Nexus website pages or rehost Nexus catalog data.

Catalog pages may be cached locally for up to six hours. The cache is capped to the 40 most recently saved page/search combinations. Expired entries are not used as live fallback data.

## Diagnostics

The local Nexus audit records endpoint paths, operation names, timestamps, application version, success/error state, request counts, and rate-limit values returned by Nexus. Secret-like fields are filtered before writing.

## Signed downloads

A Nexus-supplied signed CDN URL is downloaded without attaching the user's API key to the CDN request.

## Account-tier restrictions

The submission candidate does not circumvent Nexus restrictions. When Nexus rejects a direct API download for a free account, the application stops at an explicit registration/approval gate.
