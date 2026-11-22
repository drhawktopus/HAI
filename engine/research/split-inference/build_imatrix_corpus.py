r"""The text rigby's Qwen3.6-35B-A3B actually processes, for an imatrix routing count: the full conversation of every
rigby MoE A1 sample, rendered in the model's chat format (<|im_start|>role ... <|im_end|>), round-robin across samples
so no single exercise dominates, capped at --max-bytes. Read-only over the logs; writes one corpus file.
The A1 harness logs a placeholder `messages` list ("A1 exercise"); the conversation lives in the sample's MODEL events,
each one's `input` the whole context so far - so each sample contributes its LAST model event's input + output (the
first build read only the placeholders: 65 messages, 2.5 KB).

Runs where inspect_ai is installed (tablet-foundry on rigby):
    docker exec -i tablet-foundry python - <logs_glob> <out_path> <max_bytes> < build_imatrix_corpus.py
"""
import glob
import json
import os
import sys

from inspect_ai.log import read_eval_log

LOGS, OUT, MAX = sys.argv[1], sys.argv[2], int(sys.argv[3])


def text_of(content):
    if isinstance(content, str):
        return content
    parts = []
    for c in content or []:
        t = getattr(c, "text", None)
        if t:
            parts.append(t)
    return "\n".join(parts)


def render(msg):
    body = text_of(getattr(msg, "content", ""))
    for tc in getattr(msg, "tool_calls", None) or []:
        body += "\n<tool_call>\n%s\n</tool_call>" % json.dumps({"name": tc.function, "arguments": tc.arguments})
    role = getattr(msg, "role", "user")
    return "<|im_start|>%s\n%s<|im_end|>\n" % (role, body.strip())


files = sorted(glob.glob(LOGS))
assert files, "no logs matched %s - the glob is wrong, not the archive" % LOGS
streams = []
for f in files:
    try:
        log = read_eval_log(f, resolve_attachments=True)
    except Exception as e:                                                   # noqa: BLE001
        print("skip %s: %s" % (os.path.basename(f), type(e).__name__))
        continue
    for s in log.samples or []:
        if str(getattr(s, "id", "")).startswith("control"):
            continue
        calls = [e for e in (s.events or []) if getattr(e, "event", "") == "model"]
        if not calls:
            continue
        last = max(calls, key=lambda e: len(e.input or []))
        msgs = list(last.input or [])
        if last.output is not None and last.output.message is not None:
            msgs.append(last.output.message)
        streams.append([render(m) for m in msgs])
print("logs %d, samples %d, messages %d" % (len(files), len(streams), sum(len(x) for x in streams)))
assert streams, "matched logs but read zero samples"
out, size, i, seen, dup = [], 0, 0, set(), 0
while size < MAX and any(streams):
    s = streams[i % len(streams)]
    if s:
        m = s.pop(0)
        if m in seen:                    # the shared system prompt and repeated briefs would otherwise lead every sample
            dup += 1
        else:
            seen.add(m)
            out.append(m)
            size += len(m.encode("utf-8"))
    i += 1
print("skipped %d duplicate messages" % dup)
os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("".join(out))
print("wrote %s: %d messages, %d bytes" % (OUT, len(out), size))
