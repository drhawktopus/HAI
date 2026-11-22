"""Inside the orchestrator container, read-only: HAI's real tool pool split into what every chat carries and the rest, in the pool's
order, plus each box's real window size for the models the hot-start proof uses, printed as one JSON document on stdout."""
import json
import sys

import config
import scheduler
import tool_selection
import tools

fn = lambda t: (t.get("function") or {}).get("name")
core = [t for t in tools.HEAVY_TOOLS if fn(t) in tool_selection.CORE_TOOL_NAMES]
rest = [t for t in tools.HEAVY_TOOLS if fn(t) not in tool_selection.CORE_TOOL_NAMES]
assert len(core) > 5 and len(rest) > 20, (len(core), len(rest))
sizes = {}
for host, model in (("bighonker", "qwen3.5:9b"), ("softserve", "qwen3.5:9b"), ("mothership", "qwen3.8:27b")):
    url = scheduler.CARDS[host]["url"]
    sizes[host] = {"model": model, "ctx": config.ctx_for(url, model, None), "read_tok_s": scheduler.read_rate(host, model)}
json.dump({"core": core, "rest": rest, "sizes": sizes}, sys.stdout)
