r"""Compare the chat templates (tokenizer.chat_template) embedded in two GGUFs, header only. Prints whether they are
identical, their lengths, and every line that differs (unified diff), plus the thinking-related lines of each.

    python3 gguf_template_diff.py <a.gguf> <b.gguf>      (needs llama.cpp's gguf-py; rigby has it)
"""
import difflib
import os
import sys

sys.path.insert(0, os.path.expanduser("~/llama.cpp-rpcasync/gguf-py"))   # llama.cpp's gguf-py on the tablet head
import gguf  # noqa: E402


def template(path):
    r = gguf.GGUFReader(path)
    f = r.fields.get("tokenizer.chat_template")
    assert f is not None, "no tokenizer.chat_template in %s" % path
    return bytes(f.parts[f.data[0]]).decode("utf-8")


a, b = template(sys.argv[1]), template(sys.argv[2])
print("identical:", a == b, "| lengths", len(a), len(b))
if a != b:
    for line in difflib.unified_diff(a.splitlines(), b.splitlines(), "A", "B", lineterm="", n=1):
        print(line[:220])
for name, t in (("A", a), ("B", b)):
    hits = [l.strip()[:200] for l in t.splitlines() if "think" in l.lower() or "enable_thinking" in l]
    print("%s thinking lines (%d):" % (name, len(hits)))
    for h in hits[:12]:
        print("   ", h)
