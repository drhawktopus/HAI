# LLM Orchestrator - Code Implementation & Docker Packaging



## 2. Repo / file layout (build under `/opt/arrstack/orchestrator/` on Sundae)

```
orchestrator/
├── app.py              # FastAPI: POST /query, the hybrid pipeline
├── config.py           # env-driven settings
├── rag.py              # BM25 over Reference\, chunking
├── hosts.py            # Glances load check + least-busy host selection
├── ollama_client.py    # /api/chat call with tool-calling loop
├── tools.py            # tool schemas + impls: search_docs / get_credential / escalate
├── notes.py            # append work-log entries to Notes\
├── requirements.txt    # fastapi, uvicorn, httpx, rank-bm25
├── Dockerfile
└── .env
```

---

## 3. The non-obvious code (the parts worth getting right)

### 3a. BM25 retrieval (`rag.py`)
Keep deps light: `rank-bm25`. Chunk each Reference doc by markdown headers (`##`/`###`) so chunks are
self-contained. **Exclude `Reference\Credentials\` from this index** — secrets are gated (see 3c).

```python
# rag.py  (sketch)
import re, pathlib
from rank_bm25 import BM25Okapi

DOCS = pathlib.Path("/docs/Reference")

def _chunks():
    out = []
    for p in DOCS.glob("*.md"):                      # NOTE: glob, not rglob → skips Credentials/
        text = p.read_text(encoding="utf-8")
        # split on H2/H3 headers, keep the header with its body
        parts = re.split(r"(?=^#{2,3}\s)", text, flags=re.M)
        for part in parts:
            if part.strip():
                out.append((p.name, part.strip()))
    return out

_CORPUS = _chunks()
_BM25 = BM25Okapi([c[1].lower().split() for c in _CORPUS])

def search(query: str, k: int = 4):
    scores = _BM25.get_scores(query.lower().split())
    ranked = sorted(zip(scores, _CORPUS), key=lambda x: x[0], reverse=True)
    return [{"source": src, "text": txt, "score": round(s, 2)}
            for s, (src, txt) in ranked[:k] if s > 0]
```
> Reload note: `_CORPUS`/`_BM25` build once at import. Add a `/reload` endpoint later if you edit docs
> while it's running.

#### 3a-NS. Per-user RAG namespaces (UPDATE 2026-06-19) — current architecture

The single-corpus sketch above evolved into **isolated named namespaces**, so each audience is grounded
only in the knowledge that is theirs (foundation for the Riley nursing tutor, and so homelab docs stop
being served to non-admin users).

- **`rag.py`** now holds a dict of `_Namespace` objects (each = its own `*.md` dirs + BM25 index +
  change-signature + reload throttle). The public API gained an optional `namespace` arg:
  `search(query, k, namespace="homelab")`, `reload/maybe_reload/loaded_at(namespace=...)`, plus
  `count/chunks/configure`. `search(namespace=None)` returns `[]` (ungrounded). Default namespace
  `homelab` reproduces the prior behavior exactly.
- **`config.py`** defines the namespaces and who maps to them:
  - `RAG_NAMESPACES` — `homelab` → `Reference/` + `LLM-Project/` + `Runbook/`; `nursing` →
    `/usercorpora/riley` (per-user corpora live in the **Workspace** co-working space, not the library —
    host `Z:\Workspace\riley-tutor\corpus\` via the `Workspace` SMB share → `/mnt/workspace` →
    read-only volume `/usercorpora/riley`; `config.USERCORPORA_DIR=/usercorpora`).
  - `USER_NAMESPACE` (env-overridable, default `Riley:nursing`) + `DEFAULT_USER_NAMESPACE` (None).
  - `namespace_for_user(user)` → the owner/admins `homelab`, Riley `nursing`, everyone else `None`.
- **`tools.py`** — the `search_docs` tool is scoped to `namespace_for_user(user)`, so a nursing user's
  active searches can't surface homelab docs (ungrounded user → `[]`).
- **`app.py`** — namespace resolved per request; the RAG seed + `maybe_reload` are scoped to it; a
  separate **`NURSING_SYSTEM`** base prompt (clinical-safety-first: never invent a drug/dose/interaction/
  lab value/citation) replaces the homelab `SYSTEM` for nursing users; `/health` reports `rag_namespaces`.
- **Trust model:** profile name is self-asserted + LAN-trusted (same as routing/HA gating) — this is
  relevance isolation, **not** a hard security boundary; real secrets stay gated via `get_credential`.
- **GOTCHA (BM25):** IDF goes negative when a term appears in ~100% of a corpus, so a namespace with one
  or very few chunks returns nothing under the `score > 0` gate. Real multi-section content (many chunks)
  avoids it; this is why a lone placeholder file isn't retrievable on its own.

Adding a user's corpus = create `Z:\Workspace\<project>\corpus\` (the Workspace co-working space, already
SMB-shared and mounted read-only at `/mnt/workspace` on Sundae → bind a `…/corpus:/usercorpora/<name>:ro`
volume into the orchestrator), point a `RAG_NAMESPACES` entry at `/usercorpora/<name>`, and map the user
in `USER_NAMESPACE`. Editable from Softserve (local `Z:`); auto-reload picks up `*.md` edits with no
manual `/reload`. Do NOT place per-user corpora in the `Documentation\` library (read-as-truth only).

### 3b. Host selection by load (`hosts.py`)
v1 has one GPU host (Softserve); structure it as a list so Mothership/BigHonker drop in for Phase E.
Read Glances; treat a host as "busy" if GPU or CPU is over threshold.

```python
# hosts.py  (sketch)
import httpx
HOSTS = [
    {"name": "softserve", "ollama": "http://<softserve-address>:11434",
     "glances": "http://<softserve-address>:61208/api/4", "gpu_max": 60, "cpu_max": 85},
    # Phase E: mothership (<mothership-address>), bighonker (<bighonker-address>, WoL) ...
]

def _load(h):
    try:
        g = httpx.get(f"{h['glances']}/gpu", timeout=2).json()
        cpu = httpx.get(f"{h['glances']}/cpu", timeout=2).json()
        return g[0].get("proc", 0), cpu.get("total", 0)   # (gpu%, cpu%)
    except Exception:
        return None

def pick_host():
    for h in HOSTS:                          # simple priority order for v1
        l = _load(h)
        if l and l[0] < h["gpu_max"] and l[1] < h["cpu_max"]:
            return h
    return None                              # all busy → caller decides (queue or escalate)
```

### 3c. Gated credentials (`tools.py`)
Credentials are NOT in the RAG index. The `get_credential` tool is the ONLY way Qwen reaches them,
and it must be explicit. v1 safety: only return a credential when the user's original request clearly
warranted it (log every access to Notes so you can audit).

```python
# tools.py  (sketch)
import pathlib
CRED_DIR = pathlib.Path("/docs/Reference/Credentials")

def get_credential(name: str) -> str:
    # name like "network", "arr-stack", "ha-energy"
    f = CRED_DIR / f"{name}-credentials.md"
    if not f.exists():
        return f"No credential file named {name}. Options: " + \
               ", ".join(p.stem.replace('-credentials','') for p in CRED_DIR.glob('*-credentials.md'))
    return f.read_text(encoding="utf-8")

TOOL_SCHEMAS = [
  {"type":"function","function":{"name":"search_docs",
     "description":"Search homelab reference docs for relevant info.",
     "parameters":{"type":"object","properties":{"query":{"type":"string"}},"required":["query"]}}},
  {"type":"function","function":{"name":"get_credential",
     "description":"Fetch a gated credentials file ONLY when the task explicitly needs a secret/API key/password. Names: network, arr-stack, ha-energy.",
     "parameters":{"type":"object","properties":{"name":{"type":"string"}},"required":["name"]}}},
  {"type":"function","function":{"name":"ask_helper",
     "description":"Delegate a self-contained sub-task (summarize, draft, transform text) to the helper model llama3.1:8b. Use to offload grunt-work while you keep coordinating.",
     "parameters":{"type":"object","properties":{"subtask":{"type":"string"}},"required":["subtask"]}}},
  {"type":"function","function":{"name":"escalate_to_claude",
     "description":"RECOMMEND escalation when a task is beyond local ability. This does NOT call Claude — it returns an offer for the USER to confirm. Claude only runs when the user explicitly asks (e.g. 'claude:' prefix). Never assume permission.",
     "parameters":{"type":"object","properties":{"reason":{"type":"string"}},"required":["reason"]}}},
]
```

**`ask_helper` impl** — call llama on a *second* GPU host (fall back to the same host if only one is
up; on a single 8 GB card it'll swap models, which is slow — that's expected until Phase E):
```python
# tools.py (continued)
import httpx, hosts
def ask_helper(subtask: str) -> str:
    h = hosts.pick_helper_host()          # prefer a host != the one running Qwen; see hosts.py
    r = httpx.post(f"{h['ollama']}/api/generate", timeout=120, json={
        "model": "llama3.1:8b", "prompt": subtask, "stream": False, "keep_alive": "5m"}).json()
    return r.get("response", "")
```
(Add a simple `pick_helper_host()` to `hosts.py` that returns a free host other than the Qwen host,
or `None`/the same host if only one is available.)

### 3d. The tool-calling loop (`ollama_client.py`)
Use Ollama's `/api/chat` with `tools`. Loop: send messages → if the model returns `tool_calls`,
run them, append results as `role:"tool"` messages, send again → until it returns a plain answer
(cap at ~4 iterations to avoid loops).

```python
# ollama_client.py  (sketch)
import httpx, json
from tools import TOOL_SCHEMAS, get_credential
import rag, notes

def chat(ollama_url, model, system, user_text):
    messages = [{"role":"system","content":system},
                {"role":"user","content":user_text}]
    for _ in range(4):
        r = httpx.post(f"{ollama_url}/api/chat", timeout=120, json={
            "model": model, "messages": messages, "tools": TOOL_SCHEMAS, "stream": False,
            "keep_alive": "5m"}).json()
        msg = r["message"]; messages.append(msg)
        calls = msg.get("tool_calls")
        if not calls:
            return msg["content"], None      # plain answer
        for c in calls:
            fn = c["function"]["name"]; args = c["function"].get("arguments", {})
            if fn == "search_docs":
                result = json.dumps(rag.search(args.get("query","")))
            elif fn == "get_credential":
                result = get_credential(args.get("name",""))
                notes.log(f"credential accessed: {args.get('name')} for: {user_text[:80]}")
            elif fn == "ask_helper":
                from tools import ask_helper
                result = ask_helper(args.get("subtask",""))
            elif fn == "escalate_to_claude":
                return None, args.get("reason","escalation requested")   # signal Tier-2
            else:
                result = f"unknown tool {fn}"
            messages.append({"role":"tool","content":result})
    return "(stopped: too many tool iterations)", None
```

### 3e. The pipeline (`app.py`)
```python
# app.py  (sketch)
from fastapi import FastAPI
from pydantic import BaseModel
import rag, hosts, ollama_client, notes, config

app = FastAPI()
class Q(BaseModel): text: str

SYSTEM = ("You are the household homelab assistant. Answer from the provided homelab docs when "
          "relevant. Use tools: search_docs for more context, get_credential ONLY when a secret is "
          "explicitly required, escalate_to_claude for real coding/complex tasks. Be concise.")

@app.post("/query")
def query(q: Q):
    t = q.text.strip()
    low = t.lower()
    # 1. rule pre-filter
    if low.startswith(("claude:", "ask claude")):
        return {"route": "claude", "reason": "explicit trigger", "task": t}
    # (optional) HA device-command heuristic → {"route":"ha", ...}  (Phase D)
    # 2. seed RAG
    seed = rag.search(t, k=4)
    seed_text = "\n\n".join(f"[{c['source']}]\n{c['text']}" for c in seed)
    # 3. pick host
    h = hosts.pick_host()
    if not h:
        return {"route": "busy", "message": "all GPU hosts busy"}   # Phase E: WoL/queue
    # 4. Qwen brain
    user = f"Homelab context:\n{seed_text}\n\n---\nUser: {t}"
    answer, escalate = ollama_client.chat(h["ollama"], config.MODEL, SYSTEM, user)
    if escalate:
        # DO NOT call Claude here — explicit-only. Offer it and stop.
        return {"route": "offer_claude", "reason": escalate,
                "message": f"This looks beyond my local ability ({escalate}). Reply 'claude' to send it to Claude.",
                "pending_task": t}
    notes.log(f"answered locally ({h['name']}): {t[:80]}")
    return {"route": "local", "host": h["name"], "answer": answer, "sources": [c["source"] for c in seed]}
```

### 3f. Notes logging (`notes.py`)
```python
# notes.py  (sketch)
import pathlib, datetime
NOTES = pathlib.Path("/docs/Notes")
def log(line: str):
    day = datetime.date.today().isoformat()
    f = NOTES / f"{day}-orchestrator.md"
    ts = datetime.datetime.now().strftime("%H:%M")
    with f.open("a", encoding="utf-8") as fh:
        fh.write(f"- {ts} CT — {line}\n")
```
> Timezone gotcha: Sundae's clock is UTC. Set `TZ=America/Chicago` on the container (compose `environment`)
> so timestamps read as Central, or force `zoneinfo` in code.

---

## 4. Docker

`requirements.txt`: `fastapi`, `uvicorn[standard]`, `httpx`, `rank-bm25`

`Dockerfile`:
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py .
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8porch"]
```
(use port `8porch` → pick a real free port, e.g. **8099**.)

Add to `/opt/arrstack/docker-compose.yml`:
```yaml
  orchestrator:
    build: ./orchestrator
    container_name: orchestrator
    restart: unless-stopped
    networks: [arrnet]
    ports: ["8099:8099"]
    environment:
      - TZ=America/Chicago
      - MODEL=qwen2.5:7b
    volumes:
      - /mnt/bigshared/Documentation:/docs        # RAG read + Notes write
    # labels for Watchtower etc. as you prefer
```
Build & run:
```bash
cd /opt/arrstack && docker compose up -d --build orchestrator
docker logs orchestrator --tail 30
```

---
