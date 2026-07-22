from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
import urllib.parse
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from ..models import ModRecord


GRAPHQL_ENDPOINT = "https://api.nexusmods.com/v2/graphql"
REST_ENDPOINT = "https://api.nexusmods.com/v1"
GAME_DOMAIN = "runescapedragonwilds"
NEXUS_GAME_URL = f"https://www.nexusmods.com/{GAME_DOMAIN}/mods/"
APP_NAME = "Dragonwilds Server Manager"
APP_VERSION = "1.0.11"
PROTOCOL_VERSION = "1"
CACHE_VERSION = 7
CACHE_TTL_SECONDS = 6 * 60 * 60


class NexusError(RuntimeError):
    pass


class NexusRegistrationRequired(NexusError):
    """The requested in-manager free-account download capability is pending Nexus approval."""
    pass


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def _category_name(value) -> str:
    if isinstance(value, dict):
        value = value.get("name") or value.get("value") or value.get("category")
    return str(value or "").strip()


def _file_timestamp(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def choose_install_file(files: list[dict]) -> dict:
    """Choose a safe standalone Nexus package instead of an incremental patch."""
    available = [item for item in files if isinstance(item, dict)]
    if not available:
        raise NexusError("This mod has no downloadable files.")
    main = [
        item for item in available
        if _truthy(item.get("primary") or item.get("is_primary"))
        or _category_name(item.get("category") or item.get("category_name")).upper() == "MAIN"
    ]
    manager_ready = [
        item for item in available
        if _truthy(item.get("manager")) or _truthy(item.get("is_primary"))
    ]
    candidates = main or manager_ready or available
    category_rank = {"MAIN": 4, "OPTIONAL": 3, "MISCELLANEOUS": 2, "UPDATE": 1, "OLD_VERSION": 0}

    def rank(item: dict) -> tuple:
        category = _category_name(item.get("category") or item.get("category_name")).upper()
        file_id = item.get("fileId") or item.get("file_id") or item.get("id") or 0
        return (
            1 if _truthy(item.get("primary") or item.get("is_primary")) else 0,
            category_rank.get(category, 2),
            1 if _truthy(item.get("manager")) or _truthy(item.get("is_primary")) else 0,
            _file_timestamp(item.get("date") or item.get("uploaded_timestamp") or item.get("uploaded_time")),
            _as_int(file_id),
        )

    return max(candidates, key=rank)


def _record_from_item(item: dict, *, expected_domain: str = GAME_DOMAIN) -> ModRecord | None:
    if not isinstance(item, dict):
        return None
    game = item.get("game") or {}
    domain = str(
        (game.get("domainName") if isinstance(game, dict) else "")
        or item.get("domain_name")
        or item.get("game_domain_name")
        or expected_domain
    ).strip().casefold()
    if domain and domain != expected_domain.casefold():
        return None

    mod_id = _as_int(item.get("modId") or item.get("mod_id") or item.get("id"))
    if mod_id <= 0:
        return None

    category = (
        _category_name(item.get("modCategory"))
        or _category_name(item.get("category"))
        or item.get("category_name")
        or item.get("category_id")
        or "Uncategorized"
    )
    picture = (
        item.get("thumbnailLargeUrl")
        or item.get("thumbnailUrl")
        or item.get("pictureUrl")
        or item.get("picture_url")
        or ""
    )
    author = item.get("author") or item.get("uploaded_by") or item.get("uploader") or ""
    if isinstance(author, dict):
        author = author.get("name") or author.get("username") or ""

    return ModRecord(
        mod_id=mod_id,
        name=str(item.get("name") or item.get("mod_name") or f"Mod {mod_id}"),
        version=str(item.get("version") or item.get("mod_version") or ""),
        author=str(author),
        summary=str(item.get("summary") or item.get("description") or ""),
        category=str(category),
        picture_url=str(picture),
        downloads=_as_int(item.get("downloads") or item.get("mod_downloads") or item.get("unique_downloads")),
        endorsements=_as_int(item.get("endorsements") or item.get("endorsement_count")),
        updated_at=str(item.get("updatedAt") or item.get("updated_timestamp") or item.get("uploaded_timestamp") or ""),
    )


def _dedupe(records: Iterable[ModRecord]) -> list[ModRecord]:
    result: dict[int, ModRecord] = {}
    for record in records:
        current = result.get(record.mod_id)
        if current is None or _file_timestamp(record.updated_at) >= _file_timestamp(current.updated_at):
            result[record.mod_id] = record
    return list(result.values())


class NexusClient:
    """Official Nexus API client with schema and transport fallbacks."""

    def __init__(self, api_key: str = "", timeout: int = 20, cache_dir: Path | None = None):
        self.api_key = api_key.strip()
        self.timeout = max(5, int(timeout))
        self.user_agent = f"DragonwildsServerManagerRebuild/{APP_VERSION}"
        self.last_catalog_source = ""
        self.last_catalog_note = ""
        self.last_catalog_cache_timestamp = ""
        self.rate_limits: dict[str, str | int] = {}
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = self.cache_dir / "catalog-pages.json" if self.cache_dir else None
        self.diagnostics_dir = (self.cache_dir.parent.parent / "diagnostics") if self.cache_dir else None
        self.audit_file = self.diagnostics_dir / "nexus-api-usage.jsonl" if self.diagnostics_dir else None
        if self.diagnostics_dir:
            self.diagnostics_dir.mkdir(parents=True, exist_ok=True)

    def _audit(self, event: str, **fields) -> None:
        if not self.audit_file:
            return
        safe = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "event": event,
            "application_name": APP_NAME,
            "application_version": APP_VERSION,
            "rate_limits": self.rate_limit_snapshot(),
        }
        for key, value in fields.items():
            if key.casefold() in {"apikey", "api_key", "key", "token", "authorization"}:
                continue
            safe[key] = value
        try:
            with self.audit_file.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(safe, ensure_ascii=False) + "\n")
        except OSError:
            pass

    @staticmethod
    def _safe_endpoint(url: str) -> str:
        return urllib.parse.urlsplit(url).path

    def _base_headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Application-Name": APP_NAME,
            "Application-Version": APP_VERSION,
            "Protocol-Version": PROTOCOL_VERSION,
        }

    def _rest_headers(self) -> dict[str, str]:
        headers = self._base_headers()
        if self.api_key:
            headers["apikey"] = self.api_key
        return headers

    def _graphql_headers(self, *, authenticated: bool = False) -> dict[str, str]:
        headers = self._base_headers()
        headers["Content-Type"] = "application/json"
        if authenticated and self.api_key:
            headers["apikey"] = self.api_key
        return headers

    def _capture_rate_limits(self, headers) -> None:
        if not headers:
            return
        snapshot: dict[str, str | int] = {}
        aliases = {
            "x-rl-hourly-limit": "hourly_limit",
            "x-rl-hourly-remaining": "hourly_remaining",
            "x-rl-hourly-reset": "hourly_reset",
            "x-rl-daily-limit": "daily_limit",
            "x-rl-daily-remaining": "daily_remaining",
            "x-rl-daily-reset": "daily_reset",
        }
        try:
            items = headers.items()
        except AttributeError:
            return
        for key, value in items:
            normalized = str(key).strip().casefold()
            target = aliases.get(normalized)
            if not target:
                continue
            text = str(value or "").strip()
            try:
                snapshot[target] = int(text)
            except ValueError:
                snapshot[target] = text
        if snapshot:
            self.rate_limits.update(snapshot)
            self._audit("rate_limit_snapshot", **snapshot)

    def rate_limit_snapshot(self) -> dict[str, str | int]:
        return dict(self.rate_limits)

    def quota_low(self) -> bool:
        daily = self.rate_limits.get("daily_remaining")
        hourly = self.rate_limits.get("hourly_remaining")
        try:
            if hourly is not None and int(hourly) <= 10:
                return True
        except (TypeError, ValueError):
            pass
        try:
            if daily is not None and int(daily) <= 50:
                return True
        except (TypeError, ValueError):
            pass
        return False

    def _json(self, request: urllib.request.Request) -> dict | list:
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                self._capture_rate_limits(response.headers)
                data = response.read(16 * 1024 * 1024)
                content_type = str(response.headers.get("Content-Type") or "").casefold()
                if data.lstrip().startswith((b"<!DOCTYPE", b"<html", b"<HTML")):
                    raise NexusError("Nexus returned an HTML security/error page instead of API data.")
                if content_type and "json" not in content_type and data[:1] not in (b"{", b"["):
                    raise NexusError(f"Nexus returned an unexpected content type: {content_type}.")
                return json.loads(data.decode("utf-8-sig"))
        except urllib.error.HTTPError as exc:
            self._capture_rate_limits(getattr(exc, "headers", None))
            body = exc.read(4096).decode("utf-8", errors="replace")
            if body.lstrip().lower().startswith(("<!doctype", "<html")):
                body = "HTML security/error page"
            raise NexusError(f"Nexus returned HTTP {exc.code}: {body[:800]}") from exc
        except urllib.error.URLError as exc:
            raise NexusError(f"Could not reach Nexus Mods: {exc.reason}") from exc
        except TimeoutError as exc:
            raise NexusError("The Nexus request timed out.") from exc
        except json.JSONDecodeError as exc:
            raise NexusError("Nexus returned an invalid JSON response.") from exc

    @staticmethod
    def _validate_graphql_result(result, transport: str) -> dict:
        if not isinstance(result, dict):
            raise NexusError(f"Unexpected Nexus GraphQL response from {transport}.")
        errors = result.get("errors") or []
        if errors:
            details: list[str] = []
            for error in errors:
                if isinstance(error, dict):
                    message = str(error.get("message") or error)
                    extensions = error.get("extensions")
                    code = extensions.get("code") if isinstance(extensions, dict) else None
                    details.append(f"{code}: {message}" if code else message)
                else:
                    details.append(str(error))
            raise NexusError("Nexus GraphQL error: " + "; ".join(details))
        return result

    def _graphql_urllib(self, query: str, variables: dict, *, authenticated: bool) -> dict:
        payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
        request = urllib.request.Request(
            GRAPHQL_ENDPOINT,
            data=payload,
            headers=self._graphql_headers(authenticated=authenticated),
            method="POST",
        )
        return self._validate_graphql_result(self._json(request), "urllib transport")

    def _rest_urllib(self, url: str):
        return self._json(urllib.request.Request(url, headers=self._rest_headers()))

    def _rest_json(self, url: str):
        endpoint = self._safe_endpoint(url)
        self._audit("api_request", method="GET", endpoint=endpoint, authenticated=bool(self.api_key), transport="urllib")
        try:
            result = self._rest_urllib(url)
        except NexusError as exc:
            self._audit("api_error", method="GET", endpoint=endpoint, error=str(exc)[:500])
            raise
        self._audit("api_success", method="GET", endpoint=endpoint)
        return result

    def _graphql(self, query: str, variables: dict) -> dict:
        authenticated = bool(self.api_key)
        operation_match = re.search(r"\b(?:query|mutation)\s+([A-Za-z0-9_]+)", query)
        operation = operation_match.group(1) if operation_match else "anonymous"
        self._audit("api_request", method="POST", endpoint="/v2/graphql", operation=operation, authenticated=authenticated, transport="urllib")
        try:
            result = self._graphql_urllib(query, variables, authenticated=authenticated)
        except NexusError as exc:
            self._audit("api_error", method="POST", endpoint="/v2/graphql", operation=operation, error=str(exc)[:500])
            raise
        self._audit("api_success", method="POST", endpoint="/v2/graphql", operation=operation)
        return result

    def validate(self) -> dict:
        if not self.api_key:
            raise NexusError("Enter a Nexus Mods API key first.")
        data = self._rest_json(f"{REST_ENDPOINT}/users/validate.json")
        if not isinstance(data, dict):
            raise NexusError("Unexpected Nexus account response.")
        return data

    @staticmethod
    def _catalog_query(*, extended: bool = False) -> str:
        node_fields = """
          modId name createdAt updatedAt summary status author
          uploader { name }
          pictureUrl modCategory { name }
          version downloads endorsements
          game { domainName name id }
        """
        if extended:
            node_fields += " thumbnailLargeUrl thumbnailUrl directDownloadEnabled supportsVortex"
        return f"""
        query DragonwildsMods($filter: ModsFilter, $sort: [ModsSort!], $offset: Int, $count: Int) {{
          mods(filter: $filter, sort: $sort, offset: $offset, count: $count) {{
            nodes {{ {node_fields} }}
            nodesCount
            totalCount
          }}
        }}
        """

    @staticmethod
    def _filter_value(value: str, op: str) -> list[dict]:
        return [{"value": value, "op": op}]

    def _catalog_graphql(
        self, offset: int, count: int, search: str, sort: str, *,
        extended: bool = False, include_status: bool = True,
    ) -> tuple[list[ModRecord], int]:
        filters: dict = {"gameDomainName": self._filter_value(GAME_DOMAIN, "EQUALS")}
        if include_status:
            filters["status"] = self._filter_value("published", "EQUALS")
        if search.strip():
            filters["name"] = self._filter_value(search.strip(), "WILDCARD")
        sort_field = sort if sort in {"downloads", "endorsements", "updatedAt", "createdAt", "name"} else "downloads"
        if search.strip() and sort_field == "name":
            sort_spec = [{"relevance": {"direction": "DESC"}}]
        else:
            sort_spec = [{sort_field: {"direction": "ASC" if sort_field == "name" else "DESC"}}]
        result = self._graphql(
            self._catalog_query(extended=extended),
            {"filter": filters, "sort": sort_spec, "offset": offset, "count": count},
        )
        page = result.get("data", {}).get("mods") or {}
        if not isinstance(page, dict):
            raise NexusError("Nexus returned no mod catalog page.")
        records = [
            record for item in page.get("nodes", []) or []
            if (record := _record_from_item(item)) is not None
        ]
        return records, _as_int(page.get("totalCount"), len(records))

    def _catalog_rest_fallback(self, offset: int, count: int, search: str, sort: str) -> tuple[list[ModRecord], int]:
        if not self.api_key:
            raise NexusError("The stable Nexus REST fallback requires the connected API key.")
        # One compatibility request only. This avoids multiplying API traffic when
        # the primary GraphQL catalog is unavailable.
        url = f"{REST_ENDPOINT}/games/{GAME_DOMAIN}/mods/latest_updated.json"
        data = self._rest_json(url)
        records = [
            record for item in (data if isinstance(data, list) else [])
            if (record := _record_from_item(item)) is not None
        ]
        records = _dedupe(records)
        needle = search.strip().casefold()
        if needle:
            records = [
                record for record in records
                if needle in record.name.casefold()
                or needle in record.author.casefold()
                or needle in record.summary.casefold()
            ]
        if sort == "name":
            records.sort(key=lambda item: item.name.casefold())
        elif sort == "endorsements":
            records.sort(key=lambda item: item.endorsements, reverse=True)
        elif sort in {"updatedAt", "createdAt"}:
            records.sort(key=lambda item: _file_timestamp(item.updated_at), reverse=True)
        else:
            records.sort(key=lambda item: item.downloads, reverse=True)
        if not records:
            raise NexusError("Nexus returned no Dragonwilds mods through the single latest-updated REST fallback.")
        return records[offset:offset + count], len(records)

    def _cache_key(self, offset: int, count: int, search: str, sort: str) -> str:
        return json.dumps([offset, count, search.strip().casefold(), sort], separators=(",", ":"))

    def _read_cache(self) -> dict:
        if not self.cache_file or not self.cache_file.exists():
            return {"version": CACHE_VERSION, "pages": {}}
        try:
            if self.cache_file.stat().st_size > 64 * 1024 * 1024:
                return {"version": CACHE_VERSION, "pages": {}}
            data = json.loads(self.cache_file.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("version") != CACHE_VERSION:
                return {"version": CACHE_VERSION, "pages": {}}
            if not isinstance(data.get("pages"), dict):
                data["pages"] = {}
            return data
        except (OSError, json.JSONDecodeError):
            return {"version": CACHE_VERSION, "pages": {}}

    def _write_cache(self, offset: int, count: int, search: str, sort: str, records: list[ModRecord], total: int, source: str) -> None:
        if not self.cache_file:
            return
        data = self._read_cache()
        pages = data.setdefault("pages", {})
        pages[self._cache_key(offset, count, search, sort)] = {
            "saved_at": time.time(), "source": source, "total": int(total),
            "records": [asdict(record) for record in records],
        }
        ordered = sorted(pages.items(), key=lambda item: float(item[1].get("saved_at", 0)), reverse=True)[:40]
        data["pages"] = dict(ordered)
        temp = self.cache_file.with_suffix(".tmp")
        try:
            temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            temp.replace(self.cache_file)
        except OSError:
            temp.unlink(missing_ok=True)

    def _cached_page(self, offset: int, count: int, search: str, sort: str) -> tuple[list[ModRecord], int] | None:
        page = self._read_cache().get("pages", {}).get(self._cache_key(offset, count, search, sort))
        if not isinstance(page, dict):
            return None
        saved_at = float(page.get("saved_at") or 0)
        age = max(0.0, time.time() - saved_at) if saved_at else CACHE_TTL_SECONDS + 1
        if age > CACHE_TTL_SECONDS:
            return None
        self.last_catalog_cache_timestamp = datetime.fromtimestamp(saved_at, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        records: list[ModRecord] = []
        for item in page.get("records", []) or []:
            if not isinstance(item, dict):
                continue
            try:
                records.append(ModRecord(**{key: value for key, value in item.items() if key in ModRecord.__dataclass_fields__}))
            except (TypeError, ValueError):
                continue
        return (records, _as_int(page.get("total"), len(records))) if records else None

    def catalog_page(self, offset: int = 0, count: int = 50, search: str = "", sort: str = "downloads") -> tuple[list[ModRecord], int]:
        offset = max(0, int(offset))
        count = max(1, min(50, int(count)))
        attempts: list[str] = []
        try:
            records, total = self._catalog_graphql(offset, count, search, sort, extended=False, include_status=True)
            if records or total > 0:
                self.last_catalog_source = "Nexus GraphQL API"
                self.last_catalog_note = "single official API catalog request"
                self._write_cache(offset, count, search, sort, records, total, self.last_catalog_source)
                return records, total
            attempts.append("GraphQL API: empty page")
        except NexusError as exc:
            attempts.append(f"GraphQL API: {exc}")
        if self.quota_low():
            attempts.append("REST API fallback skipped because Nexus reported a low remaining API quota")
        else:
            try:
                records, total = self._catalog_rest_fallback(offset, count, search, sort)
                self.last_catalog_source = "Nexus REST API fallback"
                self.last_catalog_note = "single official latest-updated REST fallback request"
                self._write_cache(offset, count, search, sort, records, total, self.last_catalog_source)
                return records, total
            except NexusError as exc:
                attempts.append(f"REST API: {exc}")
        cached = self._cached_page(offset, count, search, sort)
        if cached:
            self.last_catalog_source = "Local Nexus cache"
            self.last_catalog_note = f"live official API unavailable; showing cached Nexus API data saved {self.last_catalog_cache_timestamp}"
            return cached
        raise NexusError(
            "The official Nexus API did not return a Dragonwilds catalog page. "
            "This registration-test build intentionally does not scrape Nexus website pages.\n\n"
            + "\n".join(f"• {item}" for item in attempts[-4:])
        )

    def mod_files(self, mod_id: int) -> list[dict]:
        if not self.api_key:
            raise NexusError("A Nexus API key is required to load downloadable files.")
        url = f"{REST_ENDPOINT}/games/{GAME_DOMAIN}/mods/{int(mod_id)}/files.json"
        data = self._rest_json(url)
        if not isinstance(data, dict) or not isinstance(data.get("files") or [], list):
            raise NexusError("Nexus returned an invalid file list.")
        return data.get("files") or []

    def game_id(self) -> int:
        query = f'query DragonwildsGame {{ game(domainName: "{GAME_DOMAIN}") {{ id domainName }} }}'
        result = self._graphql(query, {})
        game = result.get("data", {}).get("game") if isinstance(result, dict) else None
        if not game or not game.get("id"):
            raise NexusError("Nexus did not return the Dragonwilds game ID.")
        return int(game["id"])

    def download_links(self, mod_id: int, file_id: int) -> list[dict]:
        if not self.api_key:
            raise NexusError("A Nexus API key is required for manager downloads.")
        url = f"{REST_ENDPOINT}/games/{GAME_DOMAIN}/mods/{int(mod_id)}/files/{int(file_id)}/download_link.json"
        try:
            data = self._rest_json(url)
        except NexusError as exc:
            lowered = str(exc).casefold()
            if (
                "403" in lowered
                or "premium users only" in lowered
                or "permission to get download links" in lowered
                or "without visiting nexusmods.com" in lowered
                or "without visting nexusmods.com" in lowered
            ):
                self._audit("capability_gate", capability="in_app_free_account_download", result="requires_nexus_approval")
                raise NexusRegistrationRequired(
                    "This free-account in-manager download is intentionally disabled in the Nexus registration test build. "
                    "Dragonwilds Server Manager is requesting Nexus approval for an authorized in-application download flow; "
                    "the test build does not bypass Nexus download restrictions or open an external workaround."
                ) from exc
            raise
        if not isinstance(data, list):
            raise NexusError("Nexus did not return a valid download-link list.")
        return data

    def review_probe(self) -> dict:
        """Run a small, user-initiated API review sequence and return a redacted result."""
        account = self.validate()
        latest = self._rest_json(f"{REST_ENDPOINT}/games/{GAME_DOMAIN}/mods/latest_updated.json")
        first = latest[0] if isinstance(latest, list) and latest else {}
        mod_id = _as_int(first.get("mod_id") or first.get("modId") or first.get("id")) if isinstance(first, dict) else 0
        file_count = 0
        download_capability = "not_tested"
        if mod_id > 0:
            files = self.mod_files(mod_id)
            file_count = len(files)
            if files:
                chosen = choose_install_file(files)
                file_id = _as_int(chosen.get("file_id") or chosen.get("fileId") or chosen.get("id"))
                if file_id > 0:
                    try:
                        self.download_links(mod_id, file_id)
                        download_capability = "direct_api_available"
                    except NexusRegistrationRequired:
                        download_capability = "free_account_authorization_pending_review"
        result = {
            "account_name": str(account.get("name") or account.get("email") or "Connected account"),
            "is_premium": bool(account.get("is_premium")),
            "sample_mod_id": mod_id,
            "sample_file_count": file_count,
            "download_capability": download_capability,
            "application_name": APP_NAME,
            "application_version": APP_VERSION,
            "rate_limits": self.rate_limit_snapshot(),
        }
        self._audit("review_probe_complete", **result)
        return result

    def export_review_report(self, target: Path) -> Path:
        events: list[dict] = []
        if self.audit_file and self.audit_file.exists():
            for line in self.audit_file.read_text(encoding="utf-8", errors="replace").splitlines()[-500:]:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    events.append(item)
        counts: dict[str, int] = {}
        for item in events:
            key = f"{item.get('method', '')} {item.get('endpoint', item.get('event', 'event'))}".strip()
            counts[key] = counts.get(key, 0) + 1
        payload = {
            "application": {"name": APP_NAME, "version": APP_VERSION, "game_domain": GAME_DOMAIN},
            "review_build": True,
            "authentication": "Personal API key for testing only; stored locally with Windows DPAPI and never sent to an application server.",
            "request_headers": ["Application-Name", "Application-Version", "User-Agent"],
            "website_scraping": False,
            "background_api_use": False,
            "free_account_direct_download_bypass": False,
            "requested_capability": "Nexus-approved in-application authorization/download flow so installs can remain inside Dragonwilds Server Manager.",
            "request_counts": counts,
            "rate_limits": self.rate_limit_snapshot(),
            "cache_policy": {"ttl_seconds": CACHE_TTL_SECONDS, "max_pages": 40, "website_scraping": False},
            "recent_redacted_events": events[-100:],
        }
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return target

    def download(self, url: str, target: Path, progress: Callable[[int, int], None] | None = None) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        # The download URL is already signed by Nexus. Never forward the user's API key to CDN hosts.
        request = urllib.request.Request(url, headers={"User-Agent": self.user_agent, "Accept": "*/*"})
        temp = target.with_suffix(target.suffix + ".part")
        try:
            with urllib.request.urlopen(request, timeout=max(self.timeout, 60)) as response, temp.open("wb") as out:
                total = int(response.headers.get("Content-Length") or 0)
                done = 0
                while True:
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
            temp.replace(target)
            return target
        except Exception:
            temp.unlink(missing_ok=True)
            raise
