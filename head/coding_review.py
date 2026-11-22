"""Independent, read-only review pass for coding tasks."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

import config


REVIEW_MARKER = "INDEPENDENT_REVIEW_V1:"
SEVERITIES = {"critical", "high", "medium", "low"}
# Map common out-of-vocab severities the 27B reviewer emits onto the canonical set,
# so a single non-standard label no longer discards the entire review.
SEVERITY_ALIASES = {
    "blocker": "critical",
    "severe": "critical",
    "major": "high",
    "important": "high",
    "warning": "medium",
    "warn": "medium",
    "moderate": "medium",
    "normal": "medium",
    "minor": "low",
    "info": "low",
    "informational": "low",
    "note": "low",
    "nit": "low",
    "nitpick": "low",
    "trivial": "low",
    "suggestion": "low",
    "style": "low",
}
VERDICT_ALIASES = {
    "pass": "pass",
    "approve": "pass",
    "approved": "pass",
    "changes_requested": "changes_requested",
    "changes requested": "changes_requested",
    "fail": "changes_requested",
    "failed": "changes_requested",
    "reject": "changes_requested",
    "rejected": "changes_requested",
}
REVIEW_FORMAT = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "changes_requested"]},
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {
                        "type": "string",
                        "enum": ["critical", "high", "medium", "low"],
                    },
                    "file": {"type": "string"},
                    "line": {"type": ["integer", "null"]},
                    "explanation": {"type": "string"},
                    "suggested_test": {"type": "string"},
                },
                "required": [
                    "severity",
                    "file",
                    "line",
                    "explanation",
                    "suggested_test",
                ],
            },
        },
    },
    "required": ["verdict", "summary", "findings"],
}

REVIEW_SYSTEM = (
    "You are an independent senior code reviewer. Review only the supplied evidence. "
    "Do not assume work was completed unless the diff or test output proves it. Prioritize "
    "functional bugs, regressions, security issues, data loss, and missing tests. Do not request "
    "style-only rewrites. Return JSON matching the supplied schema. Use verdict=pass when there "
    "are no actionable findings."
)


class ReviewError(RuntimeError):
    pass


def _coding_client():
    import coding_client

    return coding_client


def _trim(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n[truncated at {limit} characters]"


def build_packet(
    workspace: dict[str, Any],
    task: dict[str, Any],
    status: dict[str, Any],
    diff: dict[str, Any],
    test_result: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build evidence without exposing the lead model's conversational reasoning."""
    packet = {
        "workspace": {
            "id": workspace.get("id"),
            "description": workspace.get("description"),
            "write": workspace.get("write"),
            "test_profiles": workspace.get("test_profiles", []),
        },
        "task": {
            "id": task.get("id"),
            "objective": task.get("objective"),
            "acceptance_criteria": task.get("acceptance_criteria", []),
            "status": task.get("status"),
            "plan": task.get("plan", []),
        },
        "git_status": _trim(status.get("stdout", ""), 8_000),
        "diff": _trim(diff.get("stdout", ""), 48_000),
        "untracked_included": diff.get("untracked_included", []),
        "untracked_skipped": diff.get("untracked_skipped", []),
    }
    if test_result is not None:
        packet["test"] = {
            "argv": test_result.get("argv", []),
            "returncode": test_result.get("returncode"),
            "timed_out": test_result.get("timed_out"),
            "stdout": _trim(test_result.get("stdout", ""), 12_000),
            "stderr": _trim(test_result.get("stderr", ""), 12_000),
        }
    return packet


def parse_review(content: str) -> dict[str, Any]:
    text = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1)
    try:
        review = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ReviewError(f"reviewer returned invalid JSON: {text[:500]!r}") from exc
    findings = review.get("findings")
    if findings is None and isinstance(review.get("review_notes"), list):
        findings = review["review_notes"]
    raw_verdict = str(review.get("verdict", "")).strip().lower()
    verdict = VERDICT_ALIASES.get(raw_verdict)
    if verdict is None and isinstance(findings, list) and findings:
        verdict = "changes_requested"
    if verdict is None:
        raise ReviewError(f"reviewer returned an invalid verdict: {text[:500]!r}")
    if findings is None and verdict == "changes_requested":
        explanation = review.get("reasoning") or review.get("summary")
        if isinstance(explanation, str) and explanation.strip():
            findings = [{
                "severity": "high",
                "file": review.get("file", ""),
                "line": review.get("line"),
                "explanation": explanation,
                "suggested_test": review.get("recommendation", ""),
            }]
    if not isinstance(findings, list):
        raise ReviewError(f"reviewer response is missing summary or findings: {text[:500]!r}")
    normalized = []
    for finding in findings[:20]:
        # Degrade gracefully: skip a malformed finding and normalize an out-of-vocab
        # severity/line rather than aborting the whole review (and losing valid findings).
        if not isinstance(finding, dict):
            continue
        raw_severity = str(finding.get("severity", "")).strip().lower()
        severity = raw_severity if raw_severity in SEVERITIES else SEVERITY_ALIASES.get(raw_severity, "low")
        line = finding.get("line")
        if not isinstance(line, int) or isinstance(line, bool) or line < 1:
            line = None
        explanation = (
            finding.get("explanation")
            or finding.get("description")
            or finding.get("message")
            or ""
        )
        suggested_test = (
            finding.get("suggested_test")
            or finding.get("recommendation")
            or finding.get("suggestion")
            or finding.get("remediation")
            or ""
        )
        normalized.append({
            "severity": severity,
            "file": str(finding.get("file", ""))[:500],
            "line": line,
            "explanation": str(explanation)[:4_000],
            "suggested_test": str(suggested_test)[:2_000],
        })
    if verdict == "pass" and normalized:
        verdict = "changes_requested"
    if verdict == "changes_requested" and not normalized:
        raise ReviewError("reviewer requested changes without a finding")
    summary = review.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        summary = normalized[0]["explanation"] if normalized else "No actionable findings."
    return {
        "verdict": verdict,
        "summary": summary[:4_000],
        "findings": normalized,
    }


def review_model(packet: dict[str, Any], *, host: str | None = None, model: str | None = None) -> dict[str, Any]:
    review_host = (host or config.CODING_REVIEW_HOST).rstrip("/")
    review_model_name = model or config.CODING_REVIEW_MODEL
    body = {
        "model": review_model_name,
        "messages": [
            {"role": "system", "content": REVIEW_SYSTEM},
            {
                "role": "user",
                "content": "Review this coding task evidence:\n" + json.dumps(packet, ensure_ascii=True),
            },
        ],
        "format": REVIEW_FORMAT,
        "stream": False,
        "think": False,
        "keep_alive": config.KEEP_ALIVE,
        "options": {"num_ctx": config.ctx_for(review_host, review_model_name, config.CODING_REVIEW_NUM_CTX)},
    }
    import httpx
    import ollama_client
    try:
        # CODING_REVIEW_TIMEOUT applies only when the host is silent; supervised tracking handles loaded hosts separately
        with ollama_client.track(review_host, review_model_name, purpose="coding review", user="system"):
            response = ollama_client.post_supervised(f"{review_host}/api/chat", json=body,
                                                     timeout=config.CODING_REVIEW_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
        content = payload["message"]["content"]
    except (httpx.HTTPError, OSError, TimeoutError, KeyError, TypeError, ValueError) as exc:
        raise ReviewError(f"review model request failed: {exc}") from exc
    result = parse_review(content)
    result["reviewer"] = {
        "host": review_host,
        "model": review_model_name,
        "independent": True,
        "write_access": False,
    }
    return result


def _note_text(note: Any) -> str:
    """A task note's words. Stored tasks hold both shapes: {"timestamp", "text"} and, from before the runner settled on one, a plain string."""
    return str(note.get("text", "") if isinstance(note, dict) else note)


def review_task(workspace_id: str, task_id: str, test_profile: str | None = None) -> dict[str, Any]:
    client = _coding_client()
    workspace = client.workspace("open", workspace_id)
    task = client.task("get", task_id=task_id)
    for value in (workspace, task):
        if value.get("error"):
            return value
    if task.get("workspace") != workspace_id:
        return {"error": "task does not belong to the requested workspace"}
    if any(_note_text(note).startswith(REVIEW_MARKER) for note in task.get("notes", [])):
        return {"error": "this task already used its independent review pass"}

    status = client.git(workspace_id, "status")
    diff = client.git(workspace_id, "diff")
    for value in (status, diff):
        if value.get("error"):
            return value
    if not diff.get("stdout", "").strip():
        worktree = task.get("worktree") if isinstance(task.get("worktree"), dict) else None
        if worktree:
            diff = client.task_diff(task_id)
            if diff.get("error"):
                return diff
        if not diff.get("stdout", "").strip():
            return {"error": "there is no diff to review"}

    test_result = client.test(workspace_id, test_profile) if test_profile else None
    if test_result and test_result.get("error"):
        return test_result
    packet = build_packet(workspace, task, status, diff, test_result)
    try:
        result = review_model(packet)
    except ReviewError as exc:
        return {"error": str(exc)}

    finding_summary = "; ".join(
        f"{item['severity']}:{item['file']}:{item['line'] or '-'}"
        for item in result["findings"]
    ) or "none"
    client.task(
        "update",
        task_id=task_id,
        note=f"{REVIEW_MARKER} verdict={result['verdict']} findings={finding_summary}",
    )
    result["task_id"] = task_id
    result["test_profile"] = test_profile
    return result
