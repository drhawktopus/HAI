"""The lab's code reaches Uhura one document per git-tracked text file, through the library's own filters: what the
public build keeps home (publish_hai.py's lists) stays out, an untracked file never appears, a secret-shaped file stays
out whole, and too few files means none (fail closed)."""
import os
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "deploy"))
import publish_library as lib  # noqa: E402

PUBLISH_HAI = '''
import re
COMPONENTS = {"head": ("orchestrator-build", ["private_ext.py", "jobs/"])}
STAY_HOME_RX = re.compile(r"\\.env$|(^|/)results?/")
'''


def _repo(root, name, files, untracked=()):
    path = os.path.join(root, name)
    os.makedirs(path)
    for rel, text in list(files.items()) + [(u, "x = 1\n") for u in untracked]:
        os.makedirs(os.path.dirname(os.path.join(path, rel)) or path, exist_ok=True)
        with open(os.path.join(path, rel), "w", encoding="utf-8") as f:
            f.write(text)
    subprocess.run(["git", "init", "-q", path], check=True)
    subprocess.run(["git", "-C", path, "add", *files], check=True)


@pytest.fixture
def code_root(monkeypatch):
    root = tempfile.mkdtemp()
    _repo(root, "hai-release", {"publish_hai.py": PUBLISH_HAI})
    _repo(root, "orchestrator-build", {"app.py": "print('hi')\n", "private_ext.py": "x = 1\n", "jobs/a.py": "x\n",
                                       "results/out.py": "x\n", "notes.md": "# Notes\n", "image.png": "PNG"},
          untracked=["scratch.py"])
    monkeypatch.setattr(lib, "CODE_REPOS", [("orchestrator-build", ""), ("hai-release", "")])
    monkeypatch.setattr(lib, "CODE_MIN_DOCS", 1)
    return root


def test_tracked_text_files_are_published_and_what_stays_home_does_not(code_root):
    names = {n for n, _t, _m in lib.code_sources(code_root)}
    assert "code--orchestrator-build--app.py.md" in names and "code--orchestrator-build--notes.md.md" in names
    for left_out in ("private_ext.py", "jobs--a.py", "results--out.py", "scratch.py", "image.png"):
        assert "code--orchestrator-build--%s.md" % left_out not in names, left_out


def test_a_notice_a_licence_and_a_manifest_are_published_and_other_bare_names_are_not(monkeypatch):
    root = tempfile.mkdtemp()
    _repo(root, "hai-release", {"publish_hai.py": PUBLISH_HAI, "NOTICE": "Required Notice\n",
                                "review/TREE-MANIFEST.tsv": "abc\tREADME.md\n", "Makefile": "all:\n"})
    monkeypatch.setattr(lib, "CODE_REPOS", [("hai-release", "")])
    monkeypatch.setattr(lib, "CODE_MIN_DOCS", 1)
    docs = {n: t for n, t, _m in lib.code_sources(root)}
    assert "Required Notice" in docs["code--hai-release--NOTICE.md"]
    assert "code--hai-release--review--TREE-MANIFEST.tsv.md" in docs
    assert "code--hai-release--Makefile.md" not in docs, "an unknown bare name stays out"


def test_the_release_candidate_is_published_whole_and_its_binaries_and_stay_home_paths_are_not(monkeypatch):
    root = tempfile.mkdtemp()
    long_text = "a,b\n" * 50
    _repo(root, "hai-release", {"publish_hai.py": PUBLISH_HAI, "review/HAI/.gitignore": "*.env\n",
                                "review/HAI/hosts/launch.vbs": "WScript.Echo 1\n", "review/HAI/menu/long.csv": long_text,
                                "review/HAI/door/Dockerfile.test": "FROM x\n", "review/HAI/tests/media/a.png": "\x00PNG\n",
                                "review/HAI/results/out.py": "x = 1\n", "elsewhere.vbs": "WScript.Echo 1\n",
                                "long_elsewhere.py": long_text})
    monkeypatch.setattr(lib, "CODE_REPOS", [("hai-release", "")])
    monkeypatch.setattr(lib, "CODE_MIN_DOCS", 1)
    monkeypatch.setattr(lib, "CODE_MAX_BYTES", 100)
    names = {n for n, _t, _m in lib.code_sources(root)}
    for whole in (".gitignore", "hosts--launch.vbs", "menu--long.csv", "door--Dockerfile.test"):
        assert "code--hai-release--review--HAI--%s.md" % whole in names, whole
    assert "code--hai-release--review--HAI--tests--media--a.png.md" not in names, "a binary file stays out"
    assert "code--hai-release--review--HAI--results--out.py.md" not in names, "what stays home stays out here too"
    assert "code--hai-release--elsewhere.vbs.md" not in names, "outside the candidate, the extension list holds"
    assert "code--hai-release--long_elsewhere.py.md" not in names, "outside the candidate, the size cap holds"


def test_a_document_carries_its_path_and_its_source_in_a_fence(code_root):
    docs = {n: t for n, t, _m in lib.code_sources(code_root)}
    body = docs["code--orchestrator-build--app.py.md"]
    assert body.startswith("# orchestrator-build/app.py\n") and "````python\nprint('hi')\n````" in body


def test_too_few_files_publish_no_code(code_root, monkeypatch):
    monkeypatch.setattr(lib, "CODE_MIN_DOCS", 1000)
    with pytest.raises(RuntimeError, match="too few code files"):
        lib.code_sources(code_root)


def test_a_secret_shaped_code_file_is_kept_out_whole():
    status, body, _w = lib.process("code--x--a.py.md", "# x/a.py\n\n````python\nKEY = 'ghp_%s'\n````\n" % ("a" * 30))
    assert status == "secret-pattern" and body is None
