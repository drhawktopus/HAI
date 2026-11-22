"""Uhura's local tests. No network and no real credentials; every path is a temp directory."""
import asyncio
import os
import types

import pytest
from starlette.testclient import TestClient

import uhura

ENV = {
    "UHURA_OWNER_EMAIL": "owner@example.com",
    "UHURA_BASE_URL": "https://uhura.example.test",
    "UHURA_GOOGLE_CLIENT_ID": "test-client.apps.googleusercontent.com",
    "UHURA_GOOGLE_CLIENT_SECRET": "test-secret",
    "UHURA_JWT_KEY": "test-signing-key-long-enough-for-this",
}


@pytest.fixture
def stack(tmp_path, monkeypatch):
    published = tmp_path / "published"
    published.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    (published / "allowed.md").write_text("MARKER-ALLOWED alpha", encoding="utf-8")
    (published / "notes.txt").write_text("MARKER-TXT", encoding="utf-8")
    sub = published / "Credentials"
    sub.mkdir()
    (sub / "inner.md").write_text("MARKER-SUBFOLDER", encoding="utf-8")
    (tmp_path / "outside.md").write_text("MARKER-OUTSIDE", encoding="utf-8")
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("UHURA_STATE_DIR", str(state))
    monkeypatch.setenv("UHURA_PUBLISHED_DIR", str(published))
    monkeypatch.setenv("FASTMCP_HOME", str(state))
    return types.SimpleNamespace(published=published, state=state, tmp=tmp_path)


@pytest.mark.parametrize("claims,expected", [
    ({"email": "owner@example.com", "email_verified": "true"}, True),
    ({"email": "Owner@Example.com", "email_verified": True}, True),
    ({"email": "owner@example.com", "email_verified": "false"}, False),
    ({"email": "owner@example.com", "email_verified": False}, False),
    ({"email": "owner@example.com"}, False),
    ({"email": "someone@example.com", "email_verified": "true"}, False),
    ({"email": "owner@example.com.evil.test", "email_verified": "true"}, False),
    ({"email_verified": "true"}, False),
    ({}, False),
    (None, False),
])
def test_owner_check_fails_closed(claims, expected):
    assert uhura.is_owner(claims, "owner@example.com") is expected


def test_owner_check_with_no_configured_owner_refuses():
    assert uhura.is_owner({"email": "", "email_verified": "true"}, "") is False


def test_listing_is_top_level_markdown_only(stack):
    assert list(uhura.Library(stack.published).listing()) == ["allowed.md"]


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
def test_symlink_is_not_part_of_the_library(stack):
    (stack.published / "link.md").symlink_to(stack.tmp / "outside.md")
    assert "link.md" not in uhura.Library(stack.published).listing()


@pytest.mark.parametrize("name", [
    "../outside.md", "..\\outside.md", "Credentials/inner.md", "Credentials\\inner.md",
    "/etc/passwd", "allowed.md/../../outside.md", "%2e%2e/outside.md", "notes.txt", "", "ALLOWED.MD",
])
def test_read_refuses_everything_outside_the_listing(stack, name):
    assert uhura.Library(stack.published).text(name) is None


def test_search_finds_the_marker_and_only_the_marker(stack):
    hits = uhura.Library(stack.published).search("MARKER", 10)
    assert [h["document"] for h in hits] == ["allowed.md"]
    assert uhura.Library(stack.published).search("MARKER-SUBFOLDER", 10)[0]["document"] == "allowed.md"
    assert "SUBFOLDER" not in hits[0]["snippet"]
    assert uhura.Library(stack.published).search("outside", 10) == []


def test_missing_secret_refuses_to_start(stack, monkeypatch):
    monkeypatch.delenv("UHURA_JWT_KEY")
    with pytest.raises(SystemExit):
        uhura.build_app()


def test_reachable_lab_address_refuses_to_start():
    import socket
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        with pytest.raises(SystemExit):
            uhura.refuse_if_lab_reachable(f"127.0.0.1:{port}")
    uhura.refuse_if_lab_reachable(f"127.0.0.1:{port}")  # closed now, so this must return


def test_watchdog_exits_when_the_lab_becomes_reachable():
    import socket
    import threading
    exited = threading.Event()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(5)
        canary = "127.0.0.1:%d" % listener.getsockname()[1]
        assert uhura.network_fault(canary) == "lab address reachable"
        uhura.start_network_watchdog(canary, every_seconds=0.01, exit_process=lambda _code: exited.set())
        assert exited.wait(5)
    assert uhura.network_fault(canary) is None


def test_who_names_the_account_and_app_and_survives_no_claims():
    token = types.SimpleNamespace(claims={"email": "owner@example.com", "uhura_app": "Claude", "uhura_client_id": "abc"})
    assert uhura.who(token) == {"email": "owner@example.com", "app": "Claude", "client_id": "abc"}
    assert uhura.who(None) == {"email": None, "app": "unknown", "client_id": None}


@pytest.mark.parametrize("current,new,expected,how", [
    ("", "hello", "hello", "first"),
    ("abc" * 30, "abc" * 10, "abc" * 30, "already-saved"),
    ("start of chat", "start of chat and more", "start of chat and more", "extended"),
    ("A" * 10 + "overlap-text-" * 5, "overlap-text-" * 5 + "new words", "A" * 10 + "overlap-text-" * 5 + "new words", "joined"),
])
def test_transcript_merge_never_loses_or_doubles_words(current, new, expected, how):
    assert uhura.merge_transcript(current, new) == (expected, how)


def test_transcript_with_no_overlap_shows_a_gap_and_keeps_both():
    merged, how = uhura.merge_transcript("first part of the chat", "completely different later part")
    assert how == "gap" and "first part" in merged and "later part" in merged and "GAP" in merged


def test_a_declared_continuation_has_no_gap_marker_and_loses_nothing():
    merged, how = uhura.merge_transcript("first part", "second part", continues=True)
    assert how == "continued" and merged == "first part\n\nsecond part"


def test_short_accidental_overlap_is_not_treated_as_a_join():
    merged, how = uhura.merge_transcript("ends with the", "the start of something")
    assert how == "gap" and merged.count("the") >= 2


def test_desk_never_overwrites_and_generates_its_own_names(tmp_path):
    desk = uhura.Desk(tmp_path)
    first = desk.add_note("career", "../../etc/passwd", "one", "ChatGPT")
    second = desk.add_note("career", "../../etc/passwd", "two", "ChatGPT")
    assert first != second and first.parent == tmp_path / "career" and second.parent == tmp_path / "career"
    assert "etc-passwd" in first.name and "one" in first.read_text(encoding="utf-8") and "two" in second.read_text(encoding="utf-8")
    assert "not a decision by the owner" in first.read_text(encoding="utf-8")


def test_journal_adds_and_keeps_earlier_entries(tmp_path):
    desk = uhura.Desk(tmp_path)
    desk.add_journal("first entry", "ChatGPT")
    path = desk.add_journal("second entry", "ChatGPT")
    body = path.read_text(encoding="utf-8")
    assert "first entry" in body and "second entry" in body and "Written by ChatGPT" in body[:300]


def test_every_upload_is_kept_as_sent(tmp_path):
    desk = uhura.Desk(tmp_path)
    desk.save_transcript("My Chat", "first upload " * 10)
    slug, how, _total = desk.save_transcript("My Chat", "completely unrelated second upload")
    assert slug == "my-chat" and how == "gap"
    assert len(list((tmp_path / "transcripts" / "my-chat" / "uploads").iterdir())) == 2
    assert desk.minutes_since_save() == 0


def test_a_retry_can_see_where_the_save_ended_and_a_resend_is_not_doubled(tmp_path):
    desk = uhura.Desk(tmp_path)
    assert desk.transcript_status("Chat")["saved"] is False
    long_text = "word " * 200
    desk.save_transcript("Chat", long_text)
    status = desk.transcript_status("Chat")
    assert status["saved"] and status["total_chars"] == len(long_text) and long_text.endswith(status["ends_with"])
    _slug, how, total = desk.save_transcript("Chat", long_text)
    assert how == "already-saved" and total == len(long_text)
    _slug, how, total = desk.save_transcript("Chat", long_text + "and more")
    assert how == "extended" and total == len(long_text) + len("and more")


def test_minutes_since_save_is_never_negative(tmp_path):
    desk = uhura.Desk(tmp_path)
    desk.save_transcript("Chat", "x")
    os.utime(tmp_path / "transcripts" / ".last-save", (9_999_999_999, 9_999_999_999))
    assert desk.minutes_since_save() == 0


def test_desk_listing_ignores_links_and_hidden_files(tmp_path):
    desk = uhura.Desk(tmp_path)
    (tmp_path / "ok.md").write_text("x", encoding="utf-8")
    (tmp_path / ".hidden").write_text("x", encoding="utf-8")
    assert list(desk.listing()) == ["ok.md"]


def test_liaison_reports_no_reply_rather_than_an_empty_success():
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.abspath(uhura.__file__)), "liaison", "liaison.py")
    spec = importlib.util.spec_from_file_location("liaison", path)
    liaison = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(liaison)
    assert liaison.reply_text({"answer": "hello"}) == "hello"
    assert liaison.reply_text({"route": "error", "message": "nope"}) == "nope"
    assert liaison.reply_text({"answer": "  "}) == ""
    assert liaison.reply_text(None) == ""
    assert liaison.USER == "ChatGPTSupervisor" and "ChatGPTSupervisor" not in liaison.os.environ.get("ADMIN_USERS", "")


def test_audit_failure_cannot_raise(tmp_path):
    audit = uhura.Audit(tmp_path / "missing-dir" / "audit.jsonl")
    audit.record("anything", name="x")
    assert audit.counts["anything"] == 1


def test_verifier_refuses_a_valid_google_token_for_someone_else(tmp_path):
    token = types.SimpleNamespace(claims={"email": "friend@example.com", "email_verified": "true"})

    class Inner:
        required_scopes = []

        async def verify_token(self, _token):
            return token

    audit = uhura.Audit(tmp_path / "audit.jsonl")
    verifier = uhura.OwnerVerifier(Inner(), "owner@example.com", audit)
    assert asyncio.run(verifier.verify_token("t")) is None
    assert audit.counts["refused_identity"] == 1
    assert "friend@example.com" in (tmp_path / "audit.jsonl").read_text(encoding="utf-8")


def test_verifier_passes_the_owner_and_a_failed_google_check_stays_failed(tmp_path):
    owner = types.SimpleNamespace(claims={"email": "owner@example.com", "email_verified": "true"})

    class Inner:
        required_scopes = []
        reply = owner

        async def verify_token(self, _token):
            return self.reply

    inner = Inner()
    verifier = uhura.OwnerVerifier(inner, "owner@example.com", uhura.Audit(tmp_path / "a.jsonl"))
    assert asyncio.run(verifier.verify_token("t")) is owner
    inner.reply = None
    assert asyncio.run(verifier.verify_token("t")) is None


def test_provider_uses_the_owner_verifier(stack):
    _mcp, provider, _audit, _lib = uhura.build_app()
    assert isinstance(provider._token_validator, uhura.OwnerVerifier)


@pytest.fixture
def client(stack):
    mcp, _provider, audit, _lib = uhura.build_app()
    app = mcp.http_app(path="/mcp", stateless_http=True)
    with TestClient(app, base_url="https://uhura.example.test") as c:
        c.audit = audit
        yield c


RPC = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
HEADERS = {"Accept": "application/json, text/event-stream"}


def test_no_token_gets_401_with_a_metadata_pointer(client):
    r = client.post("/mcp", json=RPC, headers=HEADERS)
    assert r.status_code == 401
    assert "resource_metadata" in r.headers["www-authenticate"]


@pytest.mark.parametrize("token", ["garbage", "a.b.c", "eyJhbGciOiJub25lIn0.eyJzdWIiOiJ4In0."])
def test_junk_tokens_get_401(client, token):
    r = client.post("/mcp", json=RPC, headers={**HEADERS, "Authorization": "Bearer " + token})
    assert r.status_code == 401


def test_metadata_requires_s256_and_offers_no_url_clients(client):
    meta = client.get("/.well-known/oauth-authorization-server").json()
    assert meta["code_challenge_methods_supported"] == ["S256"]
    assert not meta.get("client_id_metadata_document_supported")
    assert meta["registration_endpoint"]


def _register(client, uri):
    return client.post("/register", json={
        "client_name": "test", "redirect_uris": [uri], "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"], "token_endpoint_auth_method": "none"})


@pytest.mark.parametrize("uri", uhura.ALLOWED_REDIRECTS[:2] + ["https://chatgpt.com/connector/oauth/abc123"])
def test_known_client_return_addresses_register(client, uri):
    assert _register(client, uri).status_code in (200, 201)


@pytest.mark.parametrize("uri", [
    "https://evil.test/callback",
    "https://claude.ai.evil.test/api/mcp/auth_callback",
    "https://claude.ai@evil.test/api/mcp/auth_callback",
    "https://chatgpt.com@evil.test/connector/oauth/x",
    "https://evil.test/https://chatgpt.com/connector/oauth/x",
    "http://chatgpt.com/connector/oauth/x",
    "https://chatgpt.com/other",
    "http://localhost:9999/callback",
    "http://127.0.0.1:9999/callback",
    "javascript:alert(1)",
])
def test_authorize_refuses_any_other_return_address(client, uri):
    """A stranger may register, but must not be able to send a browser to their own address."""
    reg = _register(client, uri)
    if reg.status_code >= 400:
        return
    r = client.get("/authorize", params={
        "client_id": reg.json()["client_id"], "redirect_uri": uri, "response_type": "code", "state": "s",
        "code_challenge": "x" * 43, "code_challenge_method": "S256",
        "resource": "https://uhura.example.test/mcp"}, follow_redirects=False)
    location = r.headers.get("location", "")
    assert r.status_code >= 400 or not location.startswith(uri), (r.status_code, location)


def test_status_counts_only(client):
    body = client.get("/status").json()
    assert body["documents"] == 1
    assert "MARKER" not in str(body)


def test_transcript_tools_are_registered_and_a_missing_folder_lists_nothing(stack, monkeypatch):
    monkeypatch.setenv("UHURA_TRANSCRIPTS_DIR", str(stack.tmp / "no-such-folder"))
    mcp, _provider, _audit, _lib = uhura.build_app()
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert {"list_transcripts", "search_transcripts", "read_transcript"} <= names
    assert uhura.Library(stack.tmp / "no-such-folder").listing() == {}
    assert [e for e in _audit.counts if e == "tool_list"], "a tool-list request must be audited"


