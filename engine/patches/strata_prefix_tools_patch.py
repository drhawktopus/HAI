"""Lab patch: the hot start's shared start may end inside the tool list, on Strata's Python server (serve/server.py, 0.1.45).

A saved start is only worth keeping when the next requests share it. Strata already pins a checkpoint at an exact token
(`strata_prefix`: {"messages": K}, {"message": I, "chars": N} or {"tokens": N}), and the engine keeps it pinned. What it
could not be told is "the first K tools", and the tool list renders FIRST: 26 of 27 real first turns share only the
always-offered tools, so a primer cut at a message boundary was never shared (helper_window.HotStarts._spec gave up: "a
cut inside the tool list needs a tokenizer it has no endpoint for"). This adds {"tools": K}: the server renders the first
K tools twice, once closing the list and once with a boundary tool after them, and the start both share is the start
every list beginning with those K tools shares. The same lcp against the request's own ids as the other forms, so the pin
is always a prefix of what the engine reads.

    python strata_prefix_tools_patch.py <path to serve/server.py>

Every replacement must match exactly once, or nothing is written. Applies on top of strata_continue_patch.py."""
import sys

REPL = []

REPL.append(("""    PREFIX_END = "\\u0001strata-prefix-end\\u0001"          # marks where a message's shared part ends in the rendered prompt
""", """    PREFIX_END = "\\u0001strata-prefix-end\\u0001"          # marks where a message's shared part ends in the rendered prompt
    # LAB: a tool after the first K, so the start every list beginning with those K shares can be found ({"tools": K})
    PREFIX_TOOL = {"type": "function", "function": {"name": "strata_prefix_boundary", "description": "",
                                                    "parameters": {"type": "object", "properties": {}}}}
"""))

REPL.append(("""        keys = ("messages", "message", "chars", "tokens")
        if (not isinstance(spec, dict) or not spec or any(k not in keys for k in spec) or
                set(spec) not in ({"messages"}, {"tokens"}, {"message", "chars"})):
            raise ValueError('strata_prefix: {"messages": K} (the first K messages), {"message": I, "chars": N} (the first '
                             'N characters of message I) or {"tokens": N}')""",
             """        keys = ("messages", "message", "chars", "tokens", "tools")
        if (not isinstance(spec, dict) or not spec or any(k not in keys for k in spec) or
                set(spec) not in ({"messages"}, {"tokens"}, {"message", "chars"}, {"tools"})):
            raise ValueError('strata_prefix: {"messages": K} (the first K messages), {"message": I, "chars": N} (the first '
                             'N characters of message I), {"tokens": N} or {"tools": K} (through the first K tools)')"""))

REPL.append(("""        def head_ids(msgs):
            \"\"\"The ids the rendered `msgs` start with (a message's trailing marker cut at PREFIX_END when it is there).\"\"\"
            marked, marked_tools, changed = mark_think_literals(msgs, tools, self.literals)""",
             """        def head_ids(msgs, tl=None):
            \"\"\"The ids the rendered `msgs` start with (a message's trailing marker cut at PREFIX_END when it is there), with
            the request's tools or `tl`.\"\"\"
            marked, marked_tools, changed = mark_think_literals(msgs, tools if tl is None else tl, self.literals)"""))

REPL.append(("""        if "tokens" in spec:
            n = spec["tokens"]
        elif "messages" in spec:""",
             """        if "tools" in spec:
            k = spec["tools"]
            if not tools or k > len(tools):
                print(f"[strata] strata_prefix.tools={k} ignored: the request has {len(tools or [])} tool(s)", flush=True)
                return {}
            # the tools render first: the start every list that begins with these K shares ends where a render that closes
            # the list after them and one that goes on to another tool part (LAB, the hot start inside the tool list)
            one, two = head_ids(messages, list(tools[:k])), head_ids(messages, list(tools[:k]) + [self.PREFIX_TOOL])
            n = min(lcp(one, two), lcp(one, ids), lcp(two, ids))
        elif "tokens" in spec:
            n = spec["tokens"]
        elif "messages" in spec:"""))


def main(path):
    with open(path, encoding="utf-8", newline="") as f:
        text = f.read()
    crlf = "\r\n" in text
    if crlf:
        text = text.replace("\r\n", "\n")
    for i, (a, b) in enumerate(REPL):
        n = text.count(a)
        if n != 1:
            sys.exit("replacement %d matched %d times, nothing written: %r" % (i, n, a[:80]))
    for a, b in REPL:
        text = text.replace(a, b)
    if crlf:
        text = text.replace("\n", "\r\n")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    print("applied %d of %d replacements to %s" % (len(REPL), len(REPL), path))


if __name__ == "__main__":
    main(sys.argv[1])
