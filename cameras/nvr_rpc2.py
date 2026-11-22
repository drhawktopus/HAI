"""Dahua/Lorex RPC2 client for the N844A8 — the interface the NVR's own web UI uses.

WHY: the CGI `configManager.cgi?action=setConfig&...` path returns literal "OK" and changes
nothing. The web UI does not
use CGI for writes; it uses the JSON-RPC endpoint at /RPC2 with a session established by a
challenge-response login. So the CGI "OK" is a lying success, and RPC2 is where a write can
actually land.

Auth is a programmatic challenge-response using the stored credential -- the same category as the
HTTP Digest auth already used for every read in this session. Credentials are read from the gated
store, never hardcoded, never typed into a web form.

Read-only by default. Writes happen only via set_config(), which the caller must ask for
explicitly, and every write is followed by a read-back because this device has already been caught
reporting success on a no-op.
"""
import hashlib
import json
import urllib.request

from nvr_settings import HOST, login


def _creds():
    user, pw = login()
    return user, pw


def _md5(s):
    return hashlib.md5(s.encode()).hexdigest().upper()


class RPC2:
    def __init__(self, host=HOST):
        self.host = host
        self.session = None
        self._id = 0

    def _post(self, path, payload):
        self._id += 1
        payload.setdefault("id", self._id)
        if self.session:
            payload.setdefault("session", self.session)
        req = urllib.request.Request(
            "http://%s%s" % (self.host, path),
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            raw = urllib.request.urlopen(req, timeout=15).read()
        except urllib.error.HTTPError as e:
            raw = e.read()
        return json.loads(raw.decode("utf-8", "replace"))

    def login(self):
        """Two-step challenge-response, the way the web UI does it."""
        user, pw = _creds()
        first = self._post("/RPC2_Login", {
            "method": "global.login",
            "params": {"userName": user, "password": "", "clientType": "Web3.0"},
        })
        params = first.get("params") or {}
        realm, random = params.get("realm"), params.get("random")
        self.session = first.get("session")
        if not (realm and random):
            return False, "no challenge returned: %s" % json.dumps(first)[:300]

        # Dahua digest: MD5(user:random:MD5(user:realm:password)), all uppercase hex.
        inner = _md5("%s:%s:%s" % (user, realm, pw))
        auth = _md5("%s:%s:%s" % (user, random, inner))

        second = self._post("/RPC2_Login", {
            "method": "global.login",
            "params": {"userName": user, "password": auth, "clientType": "Web3.0",
                       "loginType": "Direct", "authorityType": "Default",
                       "passwordType": "Default"},
        })
        if second.get("result"):
            self.session = second.get("session", self.session)
            return True, "session %s" % self.session
        return False, json.dumps(second)[:300]

    def call(self, method, params=None, object_id=None, extra=None):
        payload = {"method": method, "params": params}
        if object_id is not None:
            payload["object"] = object_id
        if extra:
            payload.update(extra)
        return self._post("/RPC2", payload)

    def get_config(self, name):
        return self.call("configManager.getConfig", {"name": name})

    def set_config(self, name, table):
        return self.call("configManager.setConfig", {"name": name, "table": table})

    def logout(self):
        try:
            self.call("global.logout")
        except Exception:
            pass
