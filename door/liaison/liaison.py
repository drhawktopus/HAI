#!/usr/bin/env python3
"""Uhura's inner half. Lives in Kirk's stack and carries GPT's messages to Doc.

It holds no command authority: Kirk takes the request, Spock places the work, McCoy advises. The identity is fixed
here, so nothing arriving from the walled-off outer half can pick a different user, a pin, or an admin route.

  POST /ask        {"text": "..."}  -> {"ask_id": "...", "status": "working"}
  GET  /ask/<id>                    -> {"status": "working|done|error|unknown", "reply": "...", ...}
  GET  /health
"""
import hmac
import json
import os
import re
import secrets
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

KIRK = os.environ.get("KIRK_URL", "http://orchestrator:8099")
USER = "ChatGPTSupervisor"
STATE = os.environ.get("LIAISON_STATE", "/state")
SECRET = os.environ.get("UHURA_LIAISON_SECRET", "")
MAX_TEXT = 200_000
REFRESH_BROKER = "http://hostops:9120/transcripts/refresh"
ASK_ID = re.compile(r"^[A-Za-z0-9_-]{8,40}$")
_lock = threading.Lock()


def reply_text(result):
    """Doc's words from Kirk's final result. Says so plainly when there are none, never an empty success."""
    if not isinstance(result, dict):
        return ""
    for key in ("answer", "response", "message", "text"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def ask_path(ask_id):
    return os.path.join(STATE, "asks", ask_id + ".json")


def save_ask(ask_id, record):
    os.makedirs(os.path.join(STATE, "asks"), exist_ok=True)
    tmp = ask_path(ask_id) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(record, f)
    os.replace(tmp, ask_path(ask_id))


def kirk(path, body=None, timeout=60):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(KIRK + path, data=data, method="POST" if body is not None else "GET",
                                 headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=timeout)


def thread_id():
    """GPT's one thread on Doc's side, created once and kept, so Doc's records show the whole conversation."""
    path = os.path.join(STATE, "thread.json")
    with _lock:
        if os.path.exists(path):
            return json.load(open(path, encoding="utf-8"))["chat_id"]
        req = urllib.request.Request(f"{KIRK}/chats/new?user={urllib.parse.quote(USER)}", data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            chat_id = json.load(r)["chat"]["id"]
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"chat_id": chat_id}, f)
        return chat_id


def run_ask(ask_id, text):
    record = {"status": "working", "started": time.time()}
    try:
        chat_id = thread_id()
        record["chat_id"] = chat_id
        save_ask(ask_id, record)
        # No clock on the work: Kirk's run is server-authoritative and may take as long as it takes.
        with kirk("/query/stream", {"text": text, "user": USER, "chat_id": chat_id}, timeout=None) as stream:
            for raw in stream:
                try:
                    event = json.loads(raw)
                except ValueError:
                    continue
                if event.get("type") == "status":
                    progress = str(event.get("message", ""))[:200]
                    if progress != record.get("progress"):
                        record["progress"] = progress
                        save_ask(ask_id, record)
                elif event.get("type") == "result":
                    result = event.get("result") or {}
                    words = reply_text(result)
                    record.update(route=result.get("route"), model=result.get("model"), host=result.get("host"))
                    if result.get("route") == "error" or not words:
                        record.update(status="error", reply=words or "Doc finished without a reply.")
                    else:
                        record.update(status="done", reply=words)
                    break
        if record["status"] == "working":
            record.update(status="unknown", reply="The stream ended with no result. Doc may still be working; check the thread.")
    except Exception as exc:
        record.update(status="error", reply=f"Could not reach Doc: {type(exc).__name__}")
    record["finished"] = time.time()
    save_ask(ask_id, record)


class Handler(BaseHTTPRequestHandler):
    server_version = "uhura-liaison"
    sys_version = ""

    def log_message(self, fmt, *args):
        print(self.command, self.path.split("?")[0][:40], args[1] if len(args) > 1 else "", flush=True)

    def reply(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        return bool(SECRET) and hmac.compare_digest(self.headers.get("X-Uhura-Liaison", ""), SECRET)

    def do_GET(self):
        if self.path == "/health":
            return self.reply(200, {"up": True})
        if not self.authorized():
            return self.reply(401, {"error": "no"})
        match = re.fullmatch(r"/ask/([A-Za-z0-9_-]+)", self.path)
        if not match or not ASK_ID.match(match.group(1)) or not os.path.exists(ask_path(match.group(1))):
            return self.reply(404, {"error": "No such request."})
        record = json.load(open(ask_path(match.group(1)), encoding="utf-8"))
        return self.reply(200, record)

    def do_POST(self):
        if not self.authorized():
            return self.reply(401, {"error": "no"})
        if self.path == '/transcripts/refresh':
            size = int(self.headers.get('Content-Length') or 0)
            if not 0 < size <= 128:
                return self.reply(400, {'error': 'Invalid refresh request.'})
            try:
                payload = json.loads(self.rfile.read(size))
                if (not isinstance(payload,dict) or set(payload) not in ({'request_id'},{'request_id','action'})
                        or not isinstance(payload.get('request_id'),str) or not re.fullmatch(r'[0-9a-f]{32}',payload['request_id'])
                        or payload.get('action','trigger') not in ('trigger','status')):
                    raise ValueError()
            except (ValueError, TypeError, KeyError):
                return self.reply(400, {'error': 'Invalid refresh request.'})
            try:
                fixed={'request_id':payload['request_id'],'action':payload.get('action','trigger')}
                request = urllib.request.Request(REFRESH_BROKER, data=json.dumps(fixed).encode(), method='POST',
                    headers={'Content-Type':'application/json', 'X-Uhura-Liaison':SECRET})
                with urllib.request.urlopen(request, timeout=20) as response:
                    receipt = json.load(response)
                if (receipt.get('status')!='requested' or receipt.get('task')!='Uhura Transcripts'
                        or receipt.get('request_id')!=payload['request_id']):
                    raise ValueError()
                return self.reply(200, receipt)
            except (ValueError, TypeError, OSError):
                return self.reply(502, {'error': 'Transcript publication did not complete.'})
        if self.path != "/ask":
            return self.reply(404, {"error": "no"})
        size = int(self.headers.get("Content-Length") or 0)
        if size > MAX_TEXT * 4:
            return self.reply(413, {"error": "Too long."})
        try:
            text = json.loads(self.rfile.read(size)).get("text")
        except (ValueError, AttributeError):
            return self.reply(400, {"error": "Bad request."})
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
            return self.reply(400, {"error": "Nothing to send, or too long."})
        ask_id = secrets.token_urlsafe(12)
        save_ask(ask_id, {"status": "working", "started": time.time()})
        threading.Thread(target=run_ask, args=(ask_id, text), daemon=True).start()
        return self.reply(200, {"ask_id": ask_id, "status": "working"})


if __name__ == "__main__":
    if not SECRET:
        raise SystemExit("uhura-liaison refuses to start: UHURA_LIAISON_SECRET is not set")
    os.makedirs(STATE, exist_ok=True)
    ThreadingHTTPServer(("0.0.0.0", 8479), Handler).serve_forever()
