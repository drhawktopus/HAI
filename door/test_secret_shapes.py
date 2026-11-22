"""The whole-document SECRET rule keeps a document out when it holds a key's SHAPE. An OpenAI-style key starts a token
(`sk-…` after a space, a quote, `=` or the line's start), so a hyphenated word that merely contains "sk-" (a file name
like `desk-time-…`, `reask-what-…`) is not a key and must not keep a whole document from the reader."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "deploy"))
import publish_library as lib  # noqa: E402

KEY = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2"          # a key's shape, made up


def test_a_word_that_contains_sk_dash_is_not_a_key():
    for text in ("`Archive\\desk-time-system-time-superseded-20261114.md`",
                 "(`feedback-dont-reask-what-the-record-answers`)",
                 "the task-scheduler-wrapper-for-the-nightly-run"):
        assert not lib.SECRET.search(text), text
    status, body, _w = lib.process("x.md", "# Notes\n\nMoved to `Archive\\desk-time-system-time-superseded.md`.\n")
    assert status != "secret-pattern" and "desk-time" in body


def test_a_key_shape_is_still_caught_wherever_a_token_starts():
    for text in (KEY, "OPENAI_API_KEY=" + KEY, "key: '%s'" % KEY, 'token "%s"' % KEY, "use %s here" % KEY,
                 "export K=sk-proj-" + "x" * 24):
        assert lib.SECRET.search(text), text
    status, body, _w = lib.process("x.md", "# Notes\n\nThe key is %s.\n" % KEY)
    assert status == "secret-pattern" and body is None
