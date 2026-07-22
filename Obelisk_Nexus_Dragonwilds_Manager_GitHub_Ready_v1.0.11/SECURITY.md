# Security Policy

## Secret handling

The repository must not contain real:

- Nexus Mods API keys
- OAuth access or refresh tokens
- OAuth client secrets
- Discord bot tokens or private webhooks
- Dragonwilds server/admin passwords
- code-signing keys

The desktop application stores local secrets through its existing local secret-storage layer. Production OAuth credentials must be handled as an installed/public client and must not rely on a confidential secret embedded in the executable.

## Reporting vulnerabilities

Please report security issues privately to the project owner rather than publishing credentials or exploit details in a public GitHub issue.
