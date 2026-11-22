"""Generic *arr v3 API client for Project #11.

Sonarr, Radarr, Lidarr, and Prowlarr share the same API shape: X-Api-Key auth,
JSON endpoints, and /api/v3/command jobs that are submitted then polled. This
module is intentionally tool-agnostic; tools wrap it later with admin gating and
pending-action confirmation.
"""
import time
from urllib.parse import quote

import httpx


class ArrError(Exception):
    """Raised when an *arr request fails or returns an unexpected response."""


class ArrClient:
    def __init__(self, base_url, api_key, timeout=15.0, client=None, api_version="v3"):
        # Sonarr/Radarr/Prowlarr are API v3; Lidarr is v1 — same shapes, different prefix.
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = (api_key or "").strip()
        self.timeout = timeout
        self._client = client
        self.api_version = (api_version or "v3").strip("/")
        if not self.base_url:
            raise ArrError("base_url is required")
        if not self.api_key:
            raise ArrError("api_key is required")

    def _url(self, path):
        path = (path or "").lstrip("/")
        prefix = f"api/{self.api_version}/"
        if not path.startswith(prefix):
            path = prefix + path
        return f"{self.base_url}/{path}"

    def _headers(self):
        return {"X-Api-Key": self.api_key}

    def request(self, method, path, params=None, json_body=None):
        close = False
        client = self._client
        if client is None:
            client = httpx.Client(timeout=self.timeout)
            close = True
        try:
            response = client.request(
                method.upper(),
                self._url(path),
                params=params,
                json=json_body,
                headers=self._headers(),
                timeout=self.timeout,
            )
            response.raise_for_status()
            if not response.content:
                return None
            return response.json()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:500] if exc.response is not None else ""
            raise ArrError(f"{method.upper()} {path} failed: HTTP {exc.response.status_code}: {body}") from exc
        except Exception as exc:
            if isinstance(exc, ArrError):
                raise
            raise ArrError(f"{method.upper()} {path} failed: {exc}") from exc
        finally:
            if close:
                client.close()

    def get(self, path, params=None):
        return self.request("GET", path, params=params)

    def post(self, path, body=None, params=None):
        return self.request("POST", path, params=params, json_body=body or {})

    def put(self, path, body=None, params=None):
        return self.request("PUT", path, params=params, json_body=body or {})

    def delete(self, path, params=None):
        return self.request("DELETE", path, params=params)

    def command(self, name, body=None, wait=False, timeout=120, poll_interval=2):
        payload = dict(body or {})
        payload["name"] = name
        submitted = self.post("command", payload)
        if not wait:
            return submitted
        command_id = submitted.get("id") if isinstance(submitted, dict) else None
        if command_id is None:
            raise ArrError(f"command {name} did not return an id")
        return self.wait_command(command_id, timeout=timeout, poll_interval=poll_interval)

    def wait_command(self, command_id, timeout=120, poll_interval=2):
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            last = self.get(f"command/{quote(str(command_id))}")
            status = (last or {}).get("status")
            if status in {"completed", "failed", "aborted"}:
                return last
            time.sleep(poll_interval)
        raise ArrError(f"command {command_id} did not finish within {timeout}s; last={last}")

    def series(self, term=None):
        params = {"term": term} if term else None
        return self.get("series", params=params)

    def movies(self, term=None):
        params = {"term": term} if term else None
        return self.get("movie", params=params)

    def queue(self):
        return self.get("queue")

    def history(self, **params):
        return self.get("history", params={k: v for k, v in params.items() if v is not None})

    def release_search(self, **params):
        return self.get("release", params={k: v for k, v in params.items() if v is not None})
