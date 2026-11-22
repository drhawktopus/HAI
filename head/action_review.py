"""Tier-2 secondary semantic check for Project #11 write actions.

A SMALL model reviews whether a proposed write action matches what the user actually asked for, before the
user is shown the confirmation. This is a *secondary* signal layered on top of the deterministic risk flags
(pending_actions.assess_risk) and the human confirm — it is NEVER a gate. It is fail-soft: any error / model
unavailable returns None and the confirm flow proceeds unchanged.

Self-contained (direct httpx call, no ollama_client import) to avoid a tools<->ollama_client import cycle,
and because it needs only a single no-tools completion.
"""
import json

import httpx

import config

_SYSTEM = (
    "You are a safety reviewer for a home-server assistant. You are given the USER'S REQUEST and a PROPOSED "
    "ACTION the assistant is about to ask the user to confirm. Decide whether the proposed action MATCHES the "
    "user's request in scope and intent. Flag a mismatch if the action does substantially MORE, FEWER, or "
    "DIFFERENT things than asked — e.g. removes/deletes many items when one (or none) was asked, deletes files "
    "when only a pause was requested, or targets something the user did not mention. Do NOT flag an action "
    "that reasonably fulfills the request. Reply with ONLY a compact JSON object, no prose: "
    '{"verdict":"match"|"mismatch"|"unsure","concern":"<one short sentence; empty if match>"}'
)


def _parse(content):
    try:
        start = content.index("{")
        end = content.rindex("}") + 1
        data = json.loads(content[start:end])
    except (ValueError, json.JSONDecodeError):
        return None
    verdict = str(data.get("verdict", "")).strip().lower()
    if verdict not in ("match", "mismatch", "unsure"):
        return None
    return {"verdict": verdict, "concern": str(data.get("concern", "")).strip()[:200]}


def review_action(user_request, action_kind, action_preview):
    """Return {'verdict','concern'} or None (fail-soft). None means 'no opinion' — do not block."""
    if not config.ACTION_REVIEW_ENABLED:
        return None
    user_request = (user_request or "").strip()
    if not user_request:
        return None
    prompt = (f"USER'S REQUEST:\n{user_request[:1500]}\n\n"
              f"PROPOSED ACTION (kind={action_kind}):\n{(action_preview or '')[:1500]}")
    try:
        import ollama_client
        with ollama_client.track(config.ACTION_REVIEW_HOST, config.ACTION_REVIEW_MODEL, purpose="action review",
                                 user="system"):
            resp = ollama_client.post_supervised(
                f"{config.ACTION_REVIEW_HOST}/api/chat",
                timeout=config.ACTION_REVIEW_TIMEOUT,
                json={
                    "model": config.ACTION_REVIEW_MODEL,
                    "stream": False,
                    "think": False,
                    "messages": [
                        {"role": "system", "content": _SYSTEM},
                        {"role": "user", "content": prompt},
                    ],
                    "options": {"num_ctx": config.ctx_for(config.ACTION_REVIEW_HOST, config.ACTION_REVIEW_MODEL, 4096)},
                },
            )
        resp.raise_for_status()
        msg = resp.json().get("message") or {}
        # Some models ignore "think": False and put the answer in `thinking`.
        # We must check both keys here to capture the payload.
        content = msg.get("content") or msg.get("thinking") or ""
    except Exception:
        return None
    return _parse(content)
