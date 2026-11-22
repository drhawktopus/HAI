import unittest

import httpx

import config
import arr_config
import qbitclient


class FakeResponse:
    def __init__(self, data=None, text="", status_code=200, headers=None):
        self._data = data
        self.text = text
        self.status_code = status_code
        self.headers = headers or ({"content-type": "application/json"} if data is not None else {})
        self.content = b"" if data is None and not text else (text.encode("utf-8") if text else b"{}")

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
        self.closed = False

    def post(self, url, data=None, headers=None, timeout=None):
        self.calls.append({"method": "POST", "url": url, "data": data, "headers": headers, "timeout": timeout})
        return self.responses.pop(0)

    def request(self, method, url, params=None, data=None, files=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "params": params, "data": data, "files": files, "headers": headers, "timeout": timeout})
        return self.responses.pop(0)

    def close(self):
        self.closed = True


class QbitClientTests(unittest.TestCase):
    def test_torrents_logs_in_and_lists_with_filters(self):
        fake = FakeClient([FakeResponse(text="Ok."), FakeResponse([{"name": "test", "hash": "abc"}])])
        client = qbitclient.QbitClient("http://qbittorrent:8080/", "admin", "pw", timeout=6, client=fake)
        out = client.torrents(filter="active", category="tv-sonarr")
        self.assertEqual(out, [{"name": "test", "hash": "abc"}])
        self.assertEqual(fake.calls[0]["url"], "http://qbittorrent:8080/api/v2/auth/login")
        self.assertEqual(fake.calls[0]["data"], {"username": "admin", "password": "pw"})
        self.assertEqual(fake.calls[1]["url"], "http://qbittorrent:8080/api/v2/torrents/info")
        self.assertEqual(fake.calls[1]["params"], {"filter": "active", "category": "tv-sonarr"})

    def test_pause_resume_recheck_remove_payloads(self):
        fake = FakeClient([FakeResponse(text="Ok."), FakeResponse(), FakeResponse(), FakeResponse(), FakeResponse()])
        client = qbitclient.QbitClient("http://qbit", "u", "p", client=fake)
        client.pause(["a", "b"])
        client.resume("a")
        client.recheck(["c"])
        client.remove(["d"], delete_files=True)
        payloads = [c["data"] for c in fake.calls[1:]]
        self.assertEqual(payloads[0], {"hashes": "a|b"})
        self.assertEqual(payloads[1], {"hashes": "a"})
        self.assertEqual(payloads[2], {"hashes": "c"})
        self.assertEqual(payloads[3], {"hashes": "d", "deleteFiles": "true"})

    def test_add_url_payload(self):
        fake = FakeClient([FakeResponse(text="Ok."), FakeResponse(text="Ok.", headers={"content-type": "text/plain"})])
        client = qbitclient.QbitClient("http://qbit", "u", "p", client=fake)
        out = client.add_url("magnet:?xt=abc", category="tv-sonarr", paused=True, savepath="/downloads/test")
        self.assertEqual(out, "Ok.")
        self.assertEqual(fake.calls[1]["url"], "http://qbit/api/v2/torrents/add")
        # Sends both `paused` (v4) and `stopped` (v5) so "add paused" holds on either qBit version.
        self.assertEqual(fake.calls[1]["data"], {"urls": "magnet:?xt=abc", "paused": "true", "stopped": "true", "category": "tv-sonarr", "savepath": "/downloads/test"})

    def test_pause_resume_prefer_v5_and_fall_back_to_v4_on_404(self):
        # Exercise _action_with_fallback at the method level (mock-proof): v5 torrents/stop 404 -> v4 pause.
        client = qbitclient.QbitClient("http://qbit", "u", "p", client=FakeClient([FakeResponse(text="Ok.")]))
        client._logged_in = True
        calls = []

        def fake_request(method, path, params=None, data=None, files=None):
            calls.append(path)
            if path == "torrents/stop":
                raise qbitclient.QbitError("POST torrents/stop failed: HTTP 404: Endpoint does not exist")
            return None
        client.request = fake_request
        client.pause(["a"])
        self.assertEqual(calls, ["torrents/stop", "torrents/pause"])   # v5 first, fell back to v4
        calls.clear()
        client.resume(["a"])
        self.assertEqual(calls, ["torrents/start"])                    # v5 start works -> no fallback

    def test_bad_login_and_missing_hashes_raise(self):
        fake = FakeClient([FakeResponse(text="Fails.")])
        client = qbitclient.QbitClient("http://qbit", "u", "p", client=fake)
        with self.assertRaises(qbitclient.QbitError):
            client.torrents()
        with self.assertRaises(qbitclient.QbitError):
            qbitclient.QbitClient("http://qbit", "u", "p", client=FakeClient([FakeResponse(text="Ok.")])).pause([])

    def test_context_manager_closes_owned_client(self):
        fake = FakeClient([FakeResponse(text="Ok.")])
        client = qbitclient.QbitClient("http://qbit", "u", "p", client=fake)
        with client:
            self.assertTrue(client._logged_in)
        self.assertFalse(fake.closed)  # caller-owned clients are not closed


class QbitConfigTests(unittest.TestCase):
    def test_timeout_config_exists(self):
        self.assertIsInstance(arr_config.QBIT_TIMEOUT, float)


if __name__ == "__main__":
    unittest.main()
