# LLM Orchestrator - Testing & Handoff Strategy



## 5. Test plan (prove each layer)

```bash
# from Sundae or any LAN box
BASE=http://<sundae-address>:8099

# a) plain local answer + RAG (should cite a source doc)
curl -s $BASE/query -H 'content-type: application/json' \
  -d '{"text":"Is qBittorrent traffic going through the VPN?"}' | jq

# b) escalation trigger (rule pre-filter)
curl -s $BASE/query -d '{"text":"claude: refactor the utility import script"}' | jq
#   → {"route":"claude", ...}

# c) gated credential (explicit) — confirm Qwen calls get_credential, and Notes logs the access
curl -s $BASE/query -d '{"text":"What is the Sonarr API key? I need it for a script."}' | jq

# d) a question with no doc match (should still answer sensibly, no bogus sources)
curl -s $BASE/query -d '{"text":"What is 2+2?"}' | jq
```
Also check: `ls /mnt/bigshared/Documentation/Notes/` shows a dated log with your test entries.

**Health checks:**
- `docker logs orchestrator` — no tracebacks
- `curl http://<softserve-address>:11434/api/tags` from Sundae works (1b done)
- `ollama ps` on Softserve shows `qwen2.5:7b` at 100% GPU while a query runs

---

## 6. What to hand to Claude (the genuinely hard / risky bits)

Do the above yourself; loop Claude in for:
- Tuning the **system prompt** so Qwen uses tools correctly (when to search vs. answer vs. escalate)
  and doesn't hallucinate sources — this is iterative and benefits from review.
- The **HA device-command heuristic** in the rule pre-filter (Phase D overlaps here).
- Anything in **1b** if mirrored networking fights you (portproxy fallback is fiddly).
- The **escalation executor** (Phase F) — actually invoking `claude -p` headless on Softserve and
  returning its result; needs care around auth + rate-limiting.
