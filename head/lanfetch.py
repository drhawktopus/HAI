"""fetch_url tool: read-only HTTP GET for live LAN endpoints and public web pages.

LAN targets are strictly allow-listed by host:port. Any other target is a PUBLIC page: every address
its host resolves to must be a global internet address (no private, loopback, link-local, or CGNAT
addresses), and each redirect hop is re-checked. Public HTML is returned as text in `pages`
(`offset` reads on) so long pages are never silently cut. Fail-soft: returns a string, never raises."""
import ipaddress
import json
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
import httpx
import config
import netguard

_LAN_SUFFIXES = netguard.LAN_SUFFIXES
_TEXT_TYPES = ("text/", "json", "xml", "javascript", "x-sh", "x-python", "yaml", "toml")


_not_public = netguard.not_public
_public_refusal = netguard.public_refusal        # ONE rule, shared with the coding runner's browser (netguard.py)


def fetch_bytes(url):
    """A PUBLIC URL's body as bytes, whatever its type -> {"url", "status", "content_type", "data", "capped"} or
    {"error": why}. The same door as a public fetch_url: only http/https, every hop's host checked by netguard (so no
    home-network or tailnet address, however it is reached), the same redirect and size limits. For the fetch-to-file
    tool: an image or a data file a job needs as a FILE, never retyped by hand."""
    full = url if "://" in (url or "") else "http://" + (url or "")
    for _hop in range(config.WEB_FETCH_MAX_REDIRECTS + 1):
        u = urlparse(full)
        if u.scheme not in ("http", "https"):
            return {"error": f"Refused scheme {u.scheme!r}; only http/https GETs are allowed."}
        why = _public_refusal(u.hostname or "")
        if why:
            return {"error": f"Refused: {why}. Only public web addresses can be fetched to a file."}
        try:
            with httpx.stream("GET", full, timeout=config.WEB_FETCH_TIMEOUT, follow_redirects=False,
                              headers={"User-Agent": config.WEB_FETCH_USER_AGENT, "Accept": "*/*"}) as r:
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                    full = urljoin(full, r.headers["location"])
                    continue
                buf, got, capped = [], 0, False
                for piece in r.iter_bytes():
                    buf.append(piece)
                    got += len(piece)
                    if got > config.WEB_FETCH_MAX_DOWNLOAD:
                        capped = True
                        break
                return {"url": full, "status": r.status_code, "content_type": r.headers.get("content-type", ""),
                        "data": b"".join(buf)[: config.WEB_FETCH_MAX_DOWNLOAD], "capped": capped}
        except Exception as exc:
            return {"error": f"GET {full} failed: {exc}"}
    return {"error": f"Refused: more than {config.WEB_FETCH_MAX_REDIRECTS} redirects from {url}."}


class _Text(HTMLParser):
    _SKIP = {"script", "style", "noscript", "svg", "head", "template"}
    _BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "section",
              "article", "table", "ul", "ol", "blockquote", "hr", "dt", "dd"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self._skip, self._pre, self.title = [], 0, 0, ""
        self._in_title = False
        self.pictures, self._svg = [], None    # (url or None, what it is), in page order; the inline SVG being read

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "img" and a.get("src") and not a["src"].startswith("data:"):
            self.pictures.append((a["src"], "img" + (": " + " ".join(a["alt"].split()) if a.get("alt", "").strip() else "")))
        elif tag == "link" and "icon" in a.get("rel", "").lower() and a.get("href"):
            self.pictures.append((a["href"], "icon"))
        elif tag == "meta" and a.get("property", a.get("name", "")).lower() in ("og:image", "twitter:image") and a.get("content"):
            self.pictures.append((a["content"], a.get("property", a.get("name", "")).lower()))
        elif tag == "svg" and self._svg is None:
            self._svg = " ".join(a.get("aria-label", "").split())
        if tag == "title":
            self._in_title = True
        if tag in self._SKIP:
            self._skip += 1
        if tag == "pre":
            self._pre += 1
        if tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "svg" and self._svg is not None and self._skip == 1:
            self.pictures.append((None, self._svg))
            self._svg = None
        if tag == "title":
            self._in_title = False
        if tag in self._SKIP and self._skip:
            self._skip -= 1
        if tag == "pre" and self._pre:
            self._pre -= 1
        if tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title and self._svg is not None:
            self._svg = self._svg or " ".join(data.split())   # an inline SVG's own <title> names it
            return
        if self._in_title:
            self.title += data
        if self._skip:
            return
        self.parts.append(data if self._pre else " ".join(data.split()) + (" " if data[-1:].isspace() else ""))

    def text(self):
        lines = [ln.rstrip() for ln in "".join(self.parts).splitlines()]
        out, blank = [], 0
        for ln in lines:
            blank = blank + 1 if not ln.strip() else 0
            if blank <= 1:
                out.append(ln)
        return "\n".join(out).strip()


def _html_to_text(html, base=""):
    p = _Text()
    try:
        p.feed(html)
        p.close()
    except Exception:
        pass
    title = " ".join(p.title.split())
    body = p.text()
    # List the page's pictures, named after their text, so a logo is visible in the text extraction. One line per picture, in page order.
    seen, lines, rest, unnamed = set(), [], [], 0
    for src, what in p.pictures:
        if src is None:
            if what:
                lines.append("- an SVG drawn inside the page (%s): open the page with coding_render to see it" % what)
            else:
                unnamed += 1      # mostly icons: one line for all of them (lttlabs.com had several hundred)
            continue
        full = urljoin(base, src.strip())
        if full in seen:
            continue
        seen.add(full)
        # the pictures that name a site go before the text, the page's other pictures after it: lttlabs.com's 90 product photos
        # had pushed its text off the first page
        head = what in ("icon", "og:image", "twitter:image") or any(w in (full + " " + what).lower() for w in ("logo", "brand"))
        (lines if head else rest).append("- %s (%s)" % (full, what))
    if unnamed:
        lines.append("- %d more SVG drawing%s inside the page with no name (often icons, sometimes the logo): coding_render "
                     "the page to see them" % (unnamed, "" if unnamed == 1 else "s"))
    if lines or rest:
        # pictures before text: paginated reads drop the tail, so the end of a long page is lost if pictures are listed after the text
        body = ("## Pictures on this page (%d)\nTo see one: coding_fetch it into your workspace, then coding_look (an .svg: "
                "coding_render). Or coding_render the page itself.\n" % (len(lines) + len(rest))) + "\n".join(lines) \
            + ("\n- %d more picture%s from the page itself, listed after the text" % (len(rest), "" if len(rest) == 1 else "s")
               if rest else "") + "\n\n## Text\n" + body
        if rest:
            body += "\n\n## More pictures on this page (%d)\n" % len(rest) + "\n".join(rest)
    return f"# {title}\n\n{body}" if title else body


def _page(text, offset, label):
    size = config.WEB_FETCH_PAGE_CHARS
    total = len(text)
    offset = max(0, min(int(offset or 0), total))
    chunk = text[offset: offset + size]
    end = offset + len(chunk)
    tail = (f"\n\n[chars {offset}-{end} of {total}; call fetch_url again with offset={end} to read on]"
            if end < total else f"\n\n[chars {offset}-{end} of {total}; end of page]")
    return f"{label}\n{chunk}{tail}"


def _fetch_public(full, offset):
    url = full
    for _hop in range(config.WEB_FETCH_MAX_REDIRECTS + 1):
        u = urlparse(url)
        if u.scheme not in ("http", "https"):
            return f"Refused redirect to scheme {u.scheme!r}."
        why = _public_refusal(u.hostname or "")
        if why:
            return f"Refused: {why}. fetch_url reads public web pages and allow-listed LAN endpoints only."
        try:
            with httpx.stream("GET", url, timeout=config.WEB_FETCH_TIMEOUT, follow_redirects=False,
                              headers={"User-Agent": config.WEB_FETCH_USER_AGENT,
                                       "Accept": "text/html,text/plain,application/json,*/*;q=0.5"}) as r:
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                    url = urljoin(url, r.headers["location"])
                    continue
                ctype = r.headers.get("content-type", "").lower()
                if ctype and not any(t in ctype for t in _TEXT_TYPES):
                    return f"HTTP {r.status_code} {url}\nNot a text page ({ctype}); fetch_url reads text only."
                buf, got, capped = [], 0, False
                for piece in r.iter_bytes():
                    buf.append(piece)
                    got += len(piece)
                    if got > config.WEB_FETCH_MAX_DOWNLOAD:
                        capped = True
                        break
                raw = b"".join(buf)[: config.WEB_FETCH_MAX_DOWNLOAD]
                enc = r.encoding or "utf-8"
                status = r.status_code
        except Exception as exc:
            return f"GET {url} failed: {exc}"
        body = raw.decode(enc, errors="replace")
        if "html" in ctype or (not ctype and body.lstrip()[:15].lower().startswith(("<!doctype", "<html"))):
            body = _html_to_text(body, url)
        elif "json" in ctype:
            try:
                body = json.dumps(json.loads(body), indent=2)
            except Exception:
                pass
        label = f"HTTP {status} {url}"
        if capped:
            label += f"\n[download stopped at {config.WEB_FETCH_MAX_DOWNLOAD} bytes; the page is larger]"
        return _page(body, offset, label)
    return f"Refused: more than {config.WEB_FETCH_MAX_REDIRECTS} redirects from {full}."


def fetch_url(url=None, offset=0):
    if not url:
        return "fetch_url needs a url."
    full = url if "://" in url else "http://" + url
    try:
        u = urlparse(full)
        port = u.port or (443 if u.scheme == "https" else 80)
    except Exception:
        return f"Invalid URL: {url!r}"
    if u.scheme not in ("http", "https"):
        return f"Refused scheme {u.scheme!r}; only http/https GETs are allowed."
    host = u.hostname or ""
    hostport = f"{host}:{port}"
    if hostport not in config.LAN_FETCH_ALLOW:
        if _public_refusal(host) is None:
            return _fetch_public(full, offset)
        return (
            f"Refused: {hostport} is not on the LAN fetch allow-list "
            f"({', '.join(sorted(config.LAN_FETCH_ALLOW)) or 'none configured'}) "
            f"and is not a public internet address ({_public_refusal(host)})."
        )
    try:
        r = httpx.get(full, timeout=config.LAN_FETCH_TIMEOUT)
    except Exception as exc:
        return f"GET {full} failed: {exc}"
    body = r.text or ""
    ctype = r.headers.get("content-type", "")
    # Pretty-print JSON for readability when the whole body is present.
    if "json" in ctype and len(body) <= config.LAN_FETCH_MAX_BYTES:
        try:
            body = json.dumps(r.json(), indent=2)
        except Exception:
            pass
    truncated = len(body) > config.LAN_FETCH_MAX_BYTES
    if truncated:
        body = body[: config.LAN_FETCH_MAX_BYTES] + "\n... [truncated]"
    return f"HTTP {r.status_code} {hostport}\n{body}"
