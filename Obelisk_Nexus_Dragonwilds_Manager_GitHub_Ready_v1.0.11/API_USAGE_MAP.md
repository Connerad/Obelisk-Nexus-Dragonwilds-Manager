# Nexus API Usage Map

All Nexus calls are user-initiated from the Nexus Mods section or during an explicit reviewer test. The application does not perform background polling.

| Purpose | Endpoint / operation | Expected frequency |
|---|---|---|
| Validate tester account | `GET /v1/users/validate.json` | Once when Connect or reviewer test is clicked |
| Browse Dragonwilds catalog | `POST /v2/graphql` (`DragonwildsMods`) | One request per requested page/search |
| Catalog compatibility fallback | `GET /v1/games/runescapedragonwilds/mods/latest_updated.json` | One request only if the GraphQL catalog request fails |
| Load files for selected mod | `GET /v1/games/runescapedragonwilds/mods/{mod_id}/files.json` | Once when Install is clicked; once for a sample mod during the explicit reviewer test |
| Request direct download link | `GET /v1/games/runescapedragonwilds/mods/{mod_id}/files/{file_id}/download_link.json` | Once per user-requested install; once for the explicit reviewer capability check |

## Request controls

- One transport is used per API operation; failed requests are not replayed through curl or PowerShell.
- The REST catalog compatibility path performs exactly one fallback request.
- Catalog responses may be cached locally for six hours to reduce repeated requests.
- Rate-limit response headers, when supplied by Nexus, are recorded in redacted diagnostics and shown in the Mods connection status.
- The application does not automatically retry when a quota is exhausted.

## Explicitly not used

- No scraping of Nexus website mod listing pages.
- No en-masse collection or rehosting of Nexus metadata.
- No developer-server storage of user API keys.
- No background Nexus API polling.
- No multi-transport retry storm.
- No free-account direct-download bypass.
