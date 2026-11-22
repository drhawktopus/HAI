import unittest
from unittest.mock import patch

import httpx

import arrclient
import arr_config
import config


class FakeResponse:
    def __init__(self, data=None, status_code=200, text=""):
        self._data = data
        self.status_code = status_code
        self.text = text
        self.content = b"" if data is None else b"{}"

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "http://example.invalid")
            response = httpx.Response(self.status_code, request=request, text=self.text)
            raise httpx.HTTPStatusError("bad", request=request, response=response)


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, params=None, json=None, headers=None, timeout=None):
        self.calls.append({
            "method": method,
            "url": url,
            "params": params,
            "json": json,
            "headers": headers,
            "timeout": timeout,
        })
        return self.responses.pop(0)


class ArrClientTests(unittest.TestCase):
    def test_get_builds_v3_url_and_auth_header(self):
        fake = FakeClient([FakeResponse([{"title": "Bleach"}])])
        client = arrclient.ArrClient("http://sonarr:8989/", "secret", timeout=7, client=fake)
        out = client.get("series", params={"term": "bleach"})
        self.assertEqual(out, [{"title": "Bleach"}])
        self.assertEqual(fake.calls[0]["method"], "GET")
        self.assertEqual(fake.calls[0]["url"], "http://sonarr:8989/api/v3/series")
        self.assertEqual(fake.calls[0]["params"], {"term": "bleach"})
        self.assertEqual(fake.calls[0]["headers"], {"X-Api-Key": "secret"})
        self.assertEqual(fake.calls[0]["timeout"], 7)

    def test_post_put_delete_helpers(self):
        fake = FakeClient([FakeResponse({"ok": 1}), FakeResponse({"ok": 2}), FakeResponse(None)])
        client = arrclient.ArrClient("http://radarr:7878", "key", client=fake)
        self.assertEqual(client.post("movie", {"title": "x"}), {"ok": 1})
        self.assertEqual(client.put("movie/1", {"title": "y"}), {"ok": 2})
        self.assertIsNone(client.delete("movie/1"))
        self.assertEqual([c["method"] for c in fake.calls], ["POST", "PUT", "DELETE"])

    def test_http_error_becomes_arr_error_without_losing_status(self):
        fake = FakeClient([FakeResponse({"error": "nope"}, status_code=401, text="unauthorized")])
        client = arrclient.ArrClient("http://prowlarr:9696", "bad", client=fake)
        with self.assertRaises(arrclient.ArrError) as cm:
            client.get("indexer")
        self.assertIn("HTTP 401", str(cm.exception))

    def test_command_submit_without_wait(self):
        fake = FakeClient([FakeResponse({"id": 12, "status": "queued"})])
        client = arrclient.ArrClient("http://sonarr:8989", "key", client=fake)
        out = client.command("EpisodeSearch", {"episodeIds": [1]}, wait=False)
        self.assertEqual(out["id"], 12)
        self.assertEqual(fake.calls[0]["url"], "http://sonarr:8989/api/v3/command")
        self.assertEqual(fake.calls[0]["json"], {"episodeIds": [1], "name": "EpisodeSearch"})

    def test_command_wait_polls_until_terminal(self):
        fake = FakeClient([
            FakeResponse({"id": 12, "status": "queued"}),
            FakeResponse({"id": 12, "status": "started"}),
            FakeResponse({"id": 12, "status": "completed"}),
        ])
        client = arrclient.ArrClient("http://sonarr:8989", "key", client=fake)
        with patch("arrclient.time.sleep", lambda _s: None):
            out = client.command("SeriesSearch", {"seriesId": 3}, wait=True, timeout=5, poll_interval=0)
        self.assertEqual(out["status"], "completed")
        self.assertEqual(fake.calls[1]["url"], "http://sonarr:8989/api/v3/command/12")

    def test_missing_base_or_key_rejected(self):
        with self.assertRaises(arrclient.ArrError):
            arrclient.ArrClient("", "key")
        with self.assertRaises(arrclient.ArrError):
            arrclient.ArrClient("http://sonarr:8989", "")


class ArrConfigTests(unittest.TestCase):
    def test_urls_default_to_docker_service_names(self):
        self.assertEqual(arr_config.SONARR_URL, "http://sonarr:8989")
        self.assertEqual(arr_config.RADARR_URL, "http://radarr:7878")
        self.assertEqual(arr_config.LIDARR_URL, "http://lidarr:8686")
        self.assertEqual(arr_config.PROWLARR_URL, "http://prowlarr:9696")

    def test_api_keys_are_not_hardcoded_in_source(self):
        # Keys must come from env / mounted secret files (_secret_value), never a literal default in source.
        # (At runtime the deployed container DOES have real keys wired via /run/secrets/*, so we check the
        # source text rather than the runtime value.)
        from pathlib import Path
        src = Path(arr_config.__file__).read_text(encoding="utf-8")
        for name in ("SONARR_APIKEY", "RADARR_APIKEY", "LIDARR_APIKEY", "PROWLARR_APIKEY", "SEERR_APIKEY", "QBIT_PASS"):
            # each arr key is sourced via _secret_value(env, file) with an empty fallback — no literal key
            self.assertIn(f'{name} = _secret_value("{name}", "{name}_FILE")', src)


if __name__ == "__main__":
    unittest.main()
