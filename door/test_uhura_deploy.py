"""Tests of Uhura's deploy tools (deploy/publish_transcripts.py and the locker identifiers): they import from deploy/, the
lab's own publishing setup, so they live beside the other deploy tests."""
import os


def test_transcript_publisher_keeps_words_and_drops_tools_side_chains_and_closed_talk(tmp_path):
    import json
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "deploy"))
    import publish_transcripts as pt
    rows = [
        {"type": "user", "timestamp": "2026-11-07T10:00:00Z", "message": {"content": "Hello Claude KEEP-ONE"}},
        {"type": "assistant", "timestamp": "2026-11-07T10:01:00Z", "message": {"content": [
            {"type": "text", "text": "KEEP-TWO"}, {"type": "tool_use", "name": "Bash", "input": {"command": "DROP-TOOL"}}]}},
        {"type": "user", "timestamp": "2026-11-07T10:02:00Z", "message": {"content": [
            {"type": "tool_result", "content": "DROP-RESULT"}]}},
        {"type": "user", "isSidechain": True, "message": {"content": "DROP-SIDECHAIN"}},
        {"type": "user", "isMeta": True, "message": {"content": "DROP-META"}},
        {"type": "user", "message": {"content": "<command-name>DROP-COMMAND</command-name>"}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "the p20 corpus DROP-CLOSED"}]}},
    ]
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    _title, turns = pt.claude_turns(str(path))
    text, withheld = pt.render("t", "s", turns)
    assert "KEEP-ONE" in text and "KEEP-TWO" in text and withheld == 1
    assert not any(m in text for m in ["DROP-TOOL", "DROP-RESULT", "DROP-SIDECHAIN", "DROP-META", "DROP-COMMAND", "DROP-CLOSED"])
    assert pt.safe_title("p20 run 7", "Session x") == "Session x"
    assert pt.safe_title("Uhura door", "Session x") == "Uhura door"


def test_listed_identifiers_publish_and_every_other_locker_value_stays_withheld(tmp_path, monkeypatch):
    import hashlib
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "deploy"))
    import publish_library as p
    locker = tmp_path / "Reference" / "Credentials"
    locker.mkdir(parents=True)
    secrets = ["pw4Secret9x", "tok3nValue77", "key5Alpha22", "pin8Beta31x"]
    ident = "aa1-host-alias9"
    (locker / "test-credentials.md").write_text("".join("- `%s`\n" % v for v in secrets + [ident]), encoding="utf-8")
    listed = tmp_path / "deploy" / "locker_identifiers.sha256"
    listed.parent.mkdir()
    listed.write_text("# comment\n%s test-credentials.md\n" % hashlib.sha256(ident.encode()).hexdigest(), encoding="utf-8")
    monkeypatch.setattr(p, "ROOT", str(tmp_path))
    monkeypatch.setattr(p, "__file__", str(listed.parent / "publish_library.py"))
    pattern, count = p.locker_pattern()
    monkeypatch.setattr(p, "LOCKER", pattern)
    assert count == len(secrets)
    assert not p.block_is_private("The host alias is %s." % ident)
    for v in secrets:
        assert p.block_is_private("Use %s here." % v), v
