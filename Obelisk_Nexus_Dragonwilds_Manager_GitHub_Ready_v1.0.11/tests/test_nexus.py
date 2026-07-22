from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.models import ModRecord
from app.services.nexus_service import (
    APP_NAME,
    APP_VERSION,
    NexusClient,
    NexusError,
    NexusRegistrationRequired,
    choose_install_file,
)


class NexusTests(unittest.TestCase):
    def test_headers_identify_registered_application_candidate(self):
        client = NexusClient("personal-rest-key")
        for headers in (client._rest_headers(), client._graphql_headers(authenticated=True)):
            folded = {key.casefold(): value for key, value in headers.items()}
            self.assertEqual(folded.get("application-name"), "Dragonwilds Server Manager")
            self.assertEqual(folded.get("application-version"), "1.0.11")
            self.assertEqual(folded.get("protocol-version"), "1")
        self.assertEqual(APP_NAME, "Dragonwilds Server Manager")
        self.assertEqual(APP_VERSION, "1.0.11")

    def test_rest_uses_apikey_without_bearer_token(self):
        client = NexusClient("personal-rest-key")
        headers = {key.casefold(): value for key, value in client._rest_headers().items()}
        self.assertEqual(headers.get("apikey"), "personal-rest-key")
        self.assertNotIn("authorization", headers)

    def test_graphql_authenticated_request_uses_apikey_without_bearer(self):
        client = NexusClient("personal-rest-key")
        headers = {key.casefold(): value for key, value in client._graphql_headers(authenticated=True).items()}
        self.assertEqual(headers.get("apikey"), "personal-rest-key")
        self.assertNotIn("authorization", headers)

    def test_graphql_uses_single_transport_and_single_auth_mode(self):
        client = NexusClient("personal-key")
        calls = []

        def fake(query, variables, *, authenticated):
            calls.append(authenticated)
            return {"data": {"mods": {"nodes": [], "totalCount": 0}}}

        with mock.patch.object(client, "_graphql_urllib", side_effect=fake):
            result = client._graphql("query ReviewQuery { x }", {})
        self.assertEqual(calls, [True])
        self.assertIn("data", result)

    def test_rest_uses_single_transport_no_retry_storm(self):
        client = NexusClient("personal-key")
        calls = []

        def fake(url):
            calls.append(url)
            return {"ok": True}

        with mock.patch.object(client, "_rest_urllib", side_effect=fake):
            result = client._rest_json("https://api.nexusmods.com/v1/users/validate.json")
        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(calls), 1)


    def test_rest_fallback_uses_one_endpoint_only(self):
        client = NexusClient("test")
        calls = []
        def fake(url):
            calls.append(url)
            return [{"mod_id": 1, "name": "Only Feed"}]
        with mock.patch.object(client, "_rest_json", side_effect=fake):
            records, total = client._catalog_rest_fallback(0, 20, "", "downloads")
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].endswith("/mods/latest_updated.json"))
        self.assertEqual(total, 1)
        self.assertEqual(records[0].name, "Only Feed")

    def test_expired_cache_is_not_used(self):
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / "cache" / "nexus"
            client = NexusClient("test", cache_dir=cache)
            record = ModRecord(mod_id=42, name="Expired Mod")
            client._write_cache(0, 20, "", "downloads", [record], 1, "Nexus GraphQL API")
            data = json.loads(client.cache_file.read_text(encoding="utf-8"))
            page = next(iter(data["pages"].values()))
            page["saved_at"] = 1
            client.cache_file.write_text(json.dumps(data), encoding="utf-8")
            self.assertIsNone(client._cached_page(0, 20, "", "downloads"))

    def test_rate_limit_headers_are_captured(self):
        client = NexusClient("test")
        class FakeResponse:
            headers = {
                "Content-Type": "application/json",
                "X-RL-Daily-Remaining": "19950",
                "X-RL-Hourly-Remaining": "480",
            }
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, size=-1): return b'{"ok": true}'
        request = __import__('urllib.request').request.Request("https://api.nexusmods.com/v1/test")
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse()):
            client._json(request)
        snapshot = client.rate_limit_snapshot()
        self.assertEqual(snapshot["daily_remaining"], 19950)
        self.assertEqual(snapshot["hourly_remaining"], 480)

    def test_low_quota_skips_automatic_rest_fallback(self):
        client = NexusClient("test")
        client.rate_limits = {"daily_remaining": 20, "hourly_remaining": 9}
        with mock.patch.object(client, "_catalog_graphql", side_effect=NexusError("graphql unavailable")):
            with mock.patch.object(client, "_catalog_rest_fallback") as fallback:
                with self.assertRaises(NexusError):
                    client.catalog_page(0, 20)
        fallback.assert_not_called()

    def test_catalog_filter_shape_is_array(self):
        client = NexusClient("test")
        captured = {}

        def fake_graphql(query, variables):
            captured.update(variables)
            return {"data": {"mods": {"nodes": [], "totalCount": 0}}}

        with mock.patch.object(client, "_graphql", side_effect=fake_graphql):
            client._catalog_graphql(0, 20, "", "downloads")
        self.assertIsInstance(captured["filter"]["gameDomainName"], list)
        self.assertIsInstance(captured["filter"]["status"], list)

    def test_catalog_parses_graphql_shape(self):
        client = NexusClient("personal-rest-key")
        payload = {"data": {"mods": {"totalCount": 1, "nodes": [{
            "modId": 7,
            "name": "Test Mod",
            "version": "2.0",
            "author": "Author",
            "summary": "Summary",
            "downloads": 123,
            "endorsements": 4,
            "updatedAt": "2026-01-01T00:00:00Z",
            "modCategory": {"name": "Gameplay"},
            "thumbnailLargeUrl": "https://example/icon-large.jpg",
            "pictureUrl": "https://example/icon.jpg",
            "status": "published",
            "game": {"domainName": "runescapedragonwilds"},
        }]}}}
        with mock.patch.object(client, "_graphql", return_value=payload):
            records, total = client.catalog_page()
        self.assertEqual(total, 1)
        self.assertEqual(records[0].name, "Test Mod")
        self.assertEqual(records[0].category, "Gameplay")
        self.assertEqual(client.last_catalog_source, "Nexus GraphQL API")

    def test_catalog_falls_back_only_to_official_rest(self):
        client = NexusClient("test")
        record = ModRecord(mod_id=12, name="Fallback Mod")
        with mock.patch.object(client, "_catalog_graphql", side_effect=NexusError("schema changed")):
            with mock.patch.object(client, "_catalog_rest_fallback", return_value=([record], 1)):
                records, total = client.catalog_page()
        self.assertEqual(total, 1)
        self.assertEqual(records[0].name, "Fallback Mod")
        self.assertEqual(client.last_catalog_source, "Nexus REST API fallback")

    def test_registration_build_has_no_website_scraping_methods(self):
        client = NexusClient("test")
        self.assertFalse(hasattr(client, "_catalog_site_fallback"))
        self.assertFalse(hasattr(client, "_site_text"))
        self.assertFalse(hasattr(client, "_extract_site_mod_ids"))

    def test_catalog_uses_local_cache_after_official_api_failure(self):
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / "cache" / "nexus"
            client = NexusClient("test", cache_dir=cache)
            record = ModRecord(mod_id=42, name="Cached Mod")
            client._write_cache(0, 20, "", "downloads", [record], 1, "Nexus GraphQL API")
            with mock.patch.object(client, "_catalog_graphql", side_effect=NexusError("graphql unavailable")):
                with mock.patch.object(client, "_catalog_rest_fallback", side_effect=NexusError("rest unavailable")):
                    records, total = client.catalog_page(0, 20)
            self.assertEqual(total, 1)
            self.assertEqual(records[0].name, "Cached Mod")
            self.assertEqual(client.last_catalog_source, "Local Nexus cache")

    def test_catalog_failure_explicitly_states_no_scraping(self):
        client = NexusClient("test")
        with mock.patch.object(client, "_catalog_graphql", side_effect=NexusError("graphql failed")):
            with mock.patch.object(client, "_catalog_rest_fallback", side_effect=NexusError("rest failed")):
                with self.assertRaisesRegex(NexusError, "does not scrape"):
                    client.catalog_page()

    def test_mod_files_uses_official_rest_endpoint(self):
        client = NexusClient("test")
        captured = {}

        def fake(url):
            captured["url"] = url
            return {"files": [{"file_id": 4, "name": "Main File", "category_name": "MAIN"}]}

        with mock.patch.object(client, "_rest_json", side_effect=fake):
            files = client.mod_files(99)
        self.assertTrue(captured["url"].endswith("/games/runescapedragonwilds/mods/99/files.json"))
        self.assertEqual(files[0]["file_id"], 4)

    def test_choose_install_file_prefers_primary_main(self):
        chosen = choose_install_file([
            {"fileId": 20, "name": "Patch", "category": "UPDATE", "manager": "true", "primary": False, "date": "2026-07-12T20:00:00Z"},
            {"fileId": 10, "name": "Main", "category": "MAIN", "manager": True, "primary": True, "date": "2026-06-01T10:00:00Z"},
        ])
        self.assertEqual(chosen["name"], "Main")

    def test_free_account_restriction_becomes_registration_gate(self):
        client = NexusClient("free-user-key")
        with mock.patch.object(
            client,
            "_rest_json",
            side_effect=NexusError(
                'Nexus returned HTTP 403: {"message":"You do not have permission to get download links from the API without visiting nexusmods.com - this is for premium users only."}'
            ),
        ):
            with self.assertRaises(NexusRegistrationRequired):
                client.download_links(12, 34)

    def test_review_report_never_contains_api_key(self):
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / "cache" / "nexus"
            client = NexusClient("very-secret-api-key", cache_dir=cache)
            client._audit("api_request", method="GET", endpoint="/v1/users/validate.json", api_key="very-secret-api-key")
            target = Path(td) / "report.json"
            client.export_review_report(target)
            text = target.read_text(encoding="utf-8")
            self.assertNotIn("very-secret-api-key", text)
            payload = json.loads(text)
            self.assertFalse(payload["website_scraping"])
            self.assertFalse(payload["free_account_direct_download_bypass"])

    def test_signed_download_request_does_not_leak_api_key_to_cdn(self):
        client = NexusClient("very-secret-api-key")
        captured = {}

        class FakeResponse:
            headers = {"Content-Length": "3"}
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, size=-1):
                if captured.get("read"):
                    return b""
                captured["read"] = True
                return b"abc"

        def fake_urlopen(request, timeout=None):
            captured["headers"] = {key.casefold(): value for key, value in request.header_items()}
            return FakeResponse()

        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "mod.zip"
            with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
                client.download("https://cdn.example/signed.zip", target)
            self.assertEqual(target.read_bytes(), b"abc")
        self.assertNotIn("apikey", captured["headers"])
        self.assertIn("user-agent", captured["headers"])


if __name__ == "__main__":
    unittest.main()
