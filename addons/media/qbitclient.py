"""qBittorrent Web API client for Project #11.

This module centralizes login/session handling and endpoint details. Tool wrappers decide
what is exposed and what requires pending-action confirmation.
"""
import httpx


class QbitError(Exception):
    """Raised when qBittorrent login or API requests fail."""


class QbitClient:
    def __init__(self, base_url, username, password, timeout=15.0, client=None):
        self.base_url = (base_url or "").rstrip("/")
        self.username = username or ""
        self.password = password or ""
        self.timeout = timeout
        self._client = client
        self._owns_client = client is None
        self._logged_in = False
        if not self.base_url:
            raise QbitError("base_url is required")
        if not self.username or not self.password:
            raise QbitError("username and password are required")

    def __enter__(self):
        self.login()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    @property
    def client(self):
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout)
        return self._client

    def close(self):
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def _url(self, path):
        return f"{self.base_url}/api/v2/{path.lstrip('/')}"

    def login(self):
        try:
            response = self.client.post(
                self._url("auth/login"),
                data={"username": self.username, "password": self.password},
                headers={"Referer": self.base_url},
                timeout=self.timeout,
            )
            response.raise_for_status()
        except Exception as exc:
            raise QbitError(f"qBittorrent login failed: {exc}") from exc
        if response.text.strip().lower().rstrip(".") not in {"ok", ""}:
            raise QbitError(f"qBittorrent login failed: {response.text[:200]}")
        self._logged_in = True
        return True

    def request(self, method, path, params=None, data=None, files=None):
        if not self._logged_in:
            self.login()
        try:
            response = self.client.request(
                method.upper(),
                self._url(path),
                params=params,
                data=data,
                files=files,
                headers={"Referer": self.base_url},
                timeout=self.timeout,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise QbitError(f"{method.upper()} {path} failed: HTTP {exc.response.status_code}: {exc.response.text[:500]}") from exc
        except Exception as exc:
            raise QbitError(f"{method.upper()} {path} failed: {exc}") from exc
        if not response.content:
            return None
        content_type = response.headers.get("content-type", "")
        if "json" in content_type or response.text[:1] in ("[", "{"):
            return response.json()
        return response.text

    def torrents(self, filter=None, category=None, hashes=None):
        params = {}
        if filter:
            params["filter"] = filter
        if category:
            params["category"] = category
        if hashes:
            params["hashes"] = hashes if isinstance(hashes, str) else "|".join(hashes)
        return self.request("GET", "torrents/info", params=params)

    def _action_with_fallback(self, primary, legacy, hashes):
        # qBittorrent 5.0 (WebAPI 2.11+) renamed torrents/pause->stop and torrents/resume->start.
        # Prefer the new name; fall back to the legacy one on 404 so we work on both v4 and v5.
        data = {"hashes": _hashes(hashes)}
        try:
            return self.request("POST", f"torrents/{primary}", data=data)
        except QbitError as exc:
            if "404" in str(exc):
                return self.request("POST", f"torrents/{legacy}", data=data)
            raise

    def pause(self, hashes):
        return self._action_with_fallback("stop", "pause", hashes)

    def resume(self, hashes):
        return self._action_with_fallback("start", "resume", hashes)

    def recheck(self, hashes):
        return self.request("POST", "torrents/recheck", data={"hashes": _hashes(hashes)})

    def remove(self, hashes, delete_files=False):
        return self.request("POST", "torrents/delete", data={"hashes": _hashes(hashes), "deleteFiles": "true" if delete_files else "false"})

    def add_url(self, url, category=None, paused=False, savepath=None):
        # v5 renamed the add param `paused` -> `stopped`; send BOTH (the server ignores the one it
        # doesn't know) so "add paused" actually holds on both qBit v4 and v5.
        val = "true" if paused else "false"
        data = {"urls": url, "paused": val, "stopped": val}
        if category:
            data["category"] = category
        if savepath:
            data["savepath"] = savepath
        return self.request("POST", "torrents/add", data=data)


def _hashes(hashes):
    if isinstance(hashes, str):
        return hashes
    if not hashes:
        raise QbitError("at least one torrent hash is required")
    return "|".join(str(h) for h in hashes)
