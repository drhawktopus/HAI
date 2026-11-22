"""Overseerr (Seerr) API client for Project #11.

Overseerr exposes /api/v1 with X-Api-Key auth. This is the request layer that friends already use, so the
write side (approve/decline/add) is admin-gated + confirm-on-write at the tool layer; this module is just
the transport + a couple of summarizers.
"""
import httpx

# Overseerr/Jellyseerr request status codes (MediaRequestStatus enum)
REQUEST_STATUS = {1: "pending", 2: "approved", 3: "declined", 4: "failed", 5: "completed"}
# Overseerr media availability status codes (MediaStatus enum)
MEDIA_STATUS = {1: "unknown", 2: "pending", 3: "processing", 4: "partially available", 5: "available"}


class SeerrError(Exception):
    """Raised when an Overseerr request fails or returns an unexpected response."""


class SeerrClient:
    def __init__(self, base_url, api_key, timeout=15.0, client=None):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = (api_key or "").strip()
        self.timeout = timeout
        self._client = client
        if not self.base_url:
            raise SeerrError("base_url is required")
        if not self.api_key:
            raise SeerrError("api_key is required")

    def _url(self, path):
        path = (path or "").lstrip("/")
        if not path.startswith("api/v1/"):
            path = "api/v1/" + path
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
                method.upper(), self._url(path),
                params=params, json=json_body, headers=self._headers(), timeout=self.timeout,
            )
            response.raise_for_status()
            if not response.content:
                return None
            return response.json()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:500] if exc.response is not None else ""
            raise SeerrError(f"{method.upper()} {path} failed: HTTP {exc.response.status_code}: {body}") from exc
        except Exception as exc:
            if isinstance(exc, SeerrError):
                raise
            raise SeerrError(f"{method.upper()} {path} failed: {exc}") from exc
        finally:
            if close:
                client.close()

    def get(self, path, params=None):
        return self.request("GET", path, params=params)

    def post(self, path, body=None, params=None):
        return self.request("POST", path, params=params, json_body=body or {})

    def list_requests(self, filter=None, take=20, skip=0, sort="added"):
        params = {"take": take, "skip": skip, "sort": sort}
        if filter:
            params["filter"] = filter
        return self.get("request", params=params)

    def get_request(self, request_id):
        return self.get(f"request/{int(request_id)}")

    def approve(self, request_id):
        return self.post(f"request/{int(request_id)}/approve")

    def decline(self, request_id):
        return self.post(f"request/{int(request_id)}/decline")

    def add_request(self, media_type, media_id, seasons=None):
        body = {"mediaType": media_type, "mediaId": int(media_id)}
        if media_type == "tv" and seasons:
            body["seasons"] = seasons
        return self.post("request", body)
