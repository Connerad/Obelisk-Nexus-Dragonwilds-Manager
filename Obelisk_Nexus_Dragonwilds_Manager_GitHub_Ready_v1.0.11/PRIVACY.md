# Privacy Notice — Dragonwilds Server Manager

Dragonwilds Server Manager is a local desktop application. It does not operate a developer-controlled backend service for Nexus Mods authentication or mod installation.

## Data stored locally

The application may store server profiles, configuration, backup metadata, local mod manifests, cached Nexus API responses, diagnostic logs, and a Nexus testing API key.

On Windows, the Nexus testing API key is protected locally with Windows DPAPI. It is not included in exported profiles, review diagnostics, or application logs.

## Nexus Mods data

Nexus API requests are initiated by the user from the Mods section or by an explicit reviewer test. The application does not scrape Nexus website pages, perform background polling, or rehost Nexus metadata.

Catalog API responses may be cached locally for up to six hours to reduce repeat requests. Expired cached catalog pages are not shown as live data.

## Diagnostics

The optional Nexus review report contains application version, endpoint paths, request counts, API success/error events, cache policy information, and any rate-limit values returned by Nexus. It excludes API keys, authorization tokens, signed download URLs, and download secrets.

## Downloads

When Nexus supplies a signed file download URL, Dragonwilds Server Manager downloads that file directly to the local machine and does not forward the user's Nexus API key to the CDN host.

The Nexus submission build does not bypass Nexus account-tier restrictions. Unsupported free-account direct-download requests stop at an approval gate pending guidance from Nexus Mods.

## User control

Users can remove the local Nexus API key from the application data, clear the Nexus cache, and uninstall the application. Uninstalling the program does not silently delete dedicated-server profiles or backups.
