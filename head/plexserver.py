"""Plex Media Server action client for Project #11.

Plex runs on FAILOVER (the Windows host), NOT in the arrstack. This is the WRITE side — trigger a library
scan or metadata refresh — hitting the failover box's Plex API with an X-Plex-Token. Tautulli (plex.py) stays the
read side (check_plex). Library refresh is the safe, common op; collections/playlists are out of scope.
"""
import httpx


class PlexError(Exception):
    """Raised when a Plex Media Server request fails."""


class PlexServer:
    def __init__(self, base_url, token, timeout=15.0, client=None):
        self.base_url = (base_url or "").rstrip("/")
        self.token = (token or "").strip()
        self.timeout = timeout
        self._client = client
        if not self.base_url:
            raise PlexError("base_url is required")
        if not self.token:
            raise PlexError("token is required")

    def _headers(self):
        return {"X-Plex-Token": self.token, "Accept": "application/json"}

    def request(self, method, path, params=None):
        close = False
        client = self._client
        if client is None:
            client = httpx.Client(timeout=self.timeout)
            close = True
        try:
            response = client.request(
                method.upper(), f"{self.base_url}/{(path or '').lstrip('/')}",
                params=params, headers=self._headers(), timeout=self.timeout,
            )
            response.raise_for_status()
            ctype = response.headers.get("content-type", "")
            if "json" in ctype and response.content:
                return response.json()
            return response.text
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:300] if exc.response is not None else ""
            raise PlexError(f"{method.upper()} {path} failed: HTTP {exc.response.status_code}: {body}") from exc
        except Exception as exc:
            if isinstance(exc, PlexError):
                raise
            raise PlexError(f"{method.upper()} {path} failed: {exc}") from exc
        finally:
            if close:
                client.close()

    def sections(self):
        """List library sections → [{key, title, type}, ...]."""
        data = self.request("GET", "/library/sections")
        directories = (((data or {}).get("MediaContainer") or {}).get("Directory")) or []
        return [{"key": d.get("key"), "title": d.get("title"), "type": d.get("type")} for d in directories]

    def refresh(self, section_key=None, force=False):
        """Trigger a scan (or forced metadata refresh) for one section, or all sections when section_key is
        None. Returns the set of section keys refreshed."""
        params = {"force": 1} if force else None
        if section_key is None:
            keys = [s["key"] for s in self.sections() if s.get("key")]
        else:
            keys = [str(section_key)]
        for key in keys:
            self.request("GET", f"/library/sections/{key}/refresh", params=params)
        return keys
