"""Behavioral tests for the fetch_url tool (allow-listed LAN HTTP GET).

httpx isn't installed in the test env, so it's mocked (same pattern as test_websearch).
"""
import sys
import unittest
from unittest.mock import MagicMock


class _FakeHttpx:
    get = MagicMock()
    stream = MagicMock()


sys.modules.pop("lanfetch", None)
_real_httpx = sys.modules.get("httpx")
sys.modules["httpx"] = _FakeHttpx()


class MockResponse:
    def __init__(self, body="", status_code=200, content_type="text/plain", json_body=None):
        self.status_code = status_code
        self.text = body
        self.headers = {"content-type": content_type}
        self._json = json_body

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


import config  # noqa: E402
import lanfetch  # noqa: E402

# Restore real httpx module to avoid poisoning other tests (e.g. starlette.testclient)
if _real_httpx is not None:
    sys.modules["httpx"] = _real_httpx
else:
    sys.modules.pop("httpx", None)


class FetchUrlTests(unittest.TestCase):
    def setUp(self):
        _FakeHttpx.get.reset_mock(side_effect=True, return_value=True)
        self._orig = set(config.LAN_FETCH_ALLOW)
        config.LAN_FETCH_ALLOW = {"10.0.0.204:61208"}

    def tearDown(self):
        config.LAN_FETCH_ALLOW = self._orig

    def test_allowed_host_fetched(self):
        _FakeHttpx.get.return_value = MockResponse(
            body='{"gpu":[{"proc":42}]}', content_type="application/json",
            json_body={"gpu": [{"proc": 42}]},
        )
        out = lanfetch.fetch_url("http://10.0.0.204:61208/api/4/all")
        self.assertIn("HTTP 200", out)
        self.assertIn("proc", out)
        _FakeHttpx.get.assert_called_once()

    def test_rejects_non_allowlisted_host(self):
        out = lanfetch.fetch_url("http://10.0.0.5:80/secret")
        self.assertIn("Refused", out)
        _FakeHttpx.get.assert_not_called()

    def test_rejects_non_http_scheme(self):
        out = lanfetch.fetch_url("file:///etc/passwd")
        self.assertIn("Refused scheme", out)
        _FakeHttpx.get.assert_not_called()

    def test_bare_hostport_defaults_to_http(self):
        _FakeHttpx.get.return_value = MockResponse(body="ok")
        out = lanfetch.fetch_url("10.0.0.204:61208/api/4/cpu")
        self.assertIn("HTTP 200", out)
        _FakeHttpx.get.assert_called_once()

    def test_get_failure_is_failsoft(self):
        _FakeHttpx.get.side_effect = Exception("connection refused")
        out = lanfetch.fetch_url("http://10.0.0.204:61208/api/4/all")
        self.assertIn("failed", out.lower())


class _StreamResponse:
    def __init__(self, body=b"", status_code=200, content_type="text/html; charset=utf-8", location=None):
        self.status_code = status_code
        self.headers = {"content-type": content_type}
        if location:
            self.headers["location"] = location
        self.encoding = "utf-8"
        self._body = body

    def iter_bytes(self):
        for i in range(0, len(self._body), 1000):
            yield self._body[i:i + 1000]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _dns(table):
    def fake(host, *_a, **_k):
        if host not in table:
            raise OSError("no such host")
        return [(2, 1, 6, "", (ip, 0)) for ip in table[host]]
    return fake


class PublicPageTests(unittest.TestCase):
    """A host that is not on the LAN allow-list is read only when EVERY address it resolves to is public."""

    def setUp(self):
        _FakeHttpx.get.reset_mock(side_effect=True, return_value=True)
        _FakeHttpx.stream.reset_mock(side_effect=True, return_value=True)
        self._orig = (set(config.LAN_FETCH_ALLOW), config.WEB_FETCH_PAGE_CHARS, config.WEB_FETCH_MAX_DOWNLOAD)
        config.LAN_FETCH_ALLOW = {"10.0.0.204:61208"}
        self._dns = lanfetch.socket.getaddrinfo
        lanfetch.socket.getaddrinfo = _dns({
            "github.com": ["140.82.112.3"],
            "evil.example": ["10.0.0.205"],
            "mixed.example": ["140.82.112.3", "10.0.0.1"],
        })

    def tearDown(self):
        config.LAN_FETCH_ALLOW, config.WEB_FETCH_PAGE_CHARS, config.WEB_FETCH_MAX_DOWNLOAD = self._orig
        lanfetch.socket.getaddrinfo = self._dns

    def test_public_html_page_comes_back_as_text(self):
        html = (b"<html><head><title>Jade Recomp</title><script>var secret=1;</script></head>"
                b"<body><h1>Renderer</h1><p>OpenGL 4.6 core.</p><pre>  x = 1\n  y = 2</pre></body></html>")
        _FakeHttpx.stream.return_value = _StreamResponse(html)
        out = lanfetch.fetch_url("https://github.com/x/y")
        self.assertIn("HTTP 200 https://github.com/x/y", out)
        self.assertIn("# Jade Recomp", out)
        self.assertIn("OpenGL 4.6 core.", out)
        self.assertIn("  x = 1\n  y = 2", out)
        self.assertNotIn("secret", out)
        self.assertIn("end of page", out)
        _FakeHttpx.get.assert_not_called()

    def test_a_page_names_its_pictures_so_a_model_can_go_and_look(self):
        """Ensure images (including favicons) are extracted as metadata, not ignored."""
        html = (b'<html><head><title>LTT Labs</title><link rel="icon" href="/favicon.svg">'
                b'<meta property="og:image" content="https://cdn.example/og.png"></head><body>'
                b'<a href="/"><img src="/logo.svg" alt="LTT Labs logo"></a><p>Tech analysis</p>'
                b'<svg aria-label="Labs mark"><title>Labs mark</title><path d="M0 0"/></svg>'
                b'<img src="/logo.svg" alt="again"><img src="data:image/png;base64,AAAA"></body></html>')
        _FakeHttpx.stream.return_value = _StreamResponse(html)
        out = lanfetch.fetch_url("https://github.com/x/y")
        self.assertIn("Tech analysis", out)
        self.assertIn("Pictures on this page (4)", out)
        self.assertIn("https://github.com/logo.svg (img: LTT Labs logo)", out)
        self.assertEqual(out.count("https://github.com/logo.svg"), 1, "one picture, one line")
        self.assertIn("https://github.com/favicon.svg (icon)", out)
        self.assertIn("https://cdn.example/og.png (og:image)", out)
        self.assertIn("an SVG drawn inside the page (Labs mark)", out)
        self.assertNotIn("data:image", out, "an inline data picture is not a link to fetch")
        self.assertIn("coding_fetch", out, "it says how to see one")
        self.assertLess(out.index("Pictures on this page"), out.index("Tech analysis"), "before the text: a long page's end is not read")

    def test_unnamed_inline_drawings_fold_into_one_line(self):
        html = b"<html><body>" + b"<svg><path d='M0 0'/></svg>" * 40 + b"<p>words</p></body></html>"
        _FakeHttpx.stream.return_value = _StreamResponse(html)
        out = lanfetch.fetch_url("https://github.com/x/y")
        self.assertIn("40 more SVG drawings inside the page with no name", out)
        self.assertIn("Pictures on this page (1)", out)

    def test_a_sites_own_pictures_come_before_the_text_and_its_photos_after(self):
        html = (b'<html><body><img src="/brand/mark.png"><p>words</p>' +
                b"".join(b'<img src="/photos/p%d.jpg" alt="product %d">' % (i, i) for i in range(30)) + b"</body></html>")
        _FakeHttpx.stream.return_value = _StreamResponse(html)
        out = lanfetch.fetch_url("https://github.com/x/y")
        self.assertIn("Pictures on this page (31)", out)
        self.assertLess(out.index("https://github.com/brand/mark.png"), out.index("words"))
        self.assertIn("30 more pictures from the page itself, listed after the text", out)
        self.assertGreater(out.index("https://github.com/photos/p0.jpg"), out.index("words"))

    def test_a_page_with_no_pictures_says_nothing_about_them(self):
        _FakeHttpx.stream.return_value = _StreamResponse(b"<html><body><p>just words</p></body></html>")
        self.assertNotIn("Pictures on this page", lanfetch.fetch_url("https://github.com/x/y"))

    def test_long_page_reads_on_by_offset_and_nothing_is_cut(self):
        config.WEB_FETCH_PAGE_CHARS = 100
        text = "".join(f"line{i:04d}\n" for i in range(60)).encode()
        _FakeHttpx.stream.return_value = _StreamResponse(text, content_type="text/plain")
        first = lanfetch.fetch_url("https://github.com/raw")
        self.assertIn("offset=100", first)
        pieces, offset = [], 0
        while True:
            _FakeHttpx.stream.return_value = _StreamResponse(text, content_type="text/plain")
            out = lanfetch.fetch_url("https://github.com/raw", offset)
            body = out.split("\n", 1)[1].rsplit("\n\n[chars", 1)[0]
            pieces.append(body)
            if "end of page" in out:
                break
            offset = int(out.rsplit("offset=", 1)[1].split()[0])
        self.assertEqual("".join(pieces), text.decode())

    def test_name_resolving_to_a_lan_address_is_refused_before_any_request(self):
        out = lanfetch.fetch_url("https://evil.example/")
        self.assertIn("Refused", out)
        self.assertIn("10.0.0.205", out)
        _FakeHttpx.stream.assert_not_called()

    def test_one_private_address_among_public_ones_is_refused(self):
        out = lanfetch.fetch_url("https://mixed.example/")
        self.assertIn("Refused", out)
        _FakeHttpx.stream.assert_not_called()

    def test_tailnet_loopback_and_mapped_addresses_are_refused(self):
        tailnet = "http://%s/" % ".".join(("100", "64", "0", "1"))   # a CGNAT address, built so no real one is written
        for url in (tailnet, "http://127.0.0.1:8099/", "http://[::ffff:10.0.0.1]/",
                    "http://169.254.169.254/latest/meta-data", "http://nas.lan/", "http://localhost:8099/",
                    "http://softserve/"):
            out = lanfetch.fetch_url(url)
            self.assertIn("Refused", out, url)
        _FakeHttpx.stream.assert_not_called()
        _FakeHttpx.get.assert_not_called()

    def test_redirect_into_the_lan_is_refused(self):
        _FakeHttpx.stream.return_value = _StreamResponse(status_code=302, location="http://10.0.0.205:8099/admin")
        out = lanfetch.fetch_url("https://github.com/bounce")
        self.assertIn("Refused", out)
        self.assertEqual(_FakeHttpx.stream.call_count, 1)

    def test_redirect_between_public_hosts_is_followed(self):
        _FakeHttpx.stream.side_effect = [
            _StreamResponse(status_code=301, location="/real"),
            _StreamResponse(b"<p>arrived</p>"),
        ]
        out = lanfetch.fetch_url("https://github.com/old")
        self.assertIn("HTTP 200 https://github.com/real", out)
        self.assertIn("arrived", out)

    def test_non_text_content_is_refused(self):
        _FakeHttpx.stream.return_value = _StreamResponse(b"\x00\x01", content_type="application/zip")
        out = lanfetch.fetch_url("https://github.com/x.zip")
        self.assertIn("Not a text page", out)

    def test_download_bound_says_so_when_it_binds(self):
        config.WEB_FETCH_MAX_DOWNLOAD = 2500
        _FakeHttpx.stream.return_value = _StreamResponse(b"a" * 9000, content_type="text/plain")
        out = lanfetch.fetch_url("https://github.com/big")
        self.assertIn("download stopped at 2500 bytes", out)

    def test_requests_are_get_only_and_never_follow_redirects_blindly(self):
        _FakeHttpx.stream.return_value = _StreamResponse(b"<p>ok</p>")
        lanfetch.fetch_url("https://github.com/x")
        args, kwargs = _FakeHttpx.stream.call_args
        self.assertEqual(args[0], "GET")
        self.assertIs(kwargs.get("follow_redirects"), False)


if __name__ == "__main__":
    unittest.main()
