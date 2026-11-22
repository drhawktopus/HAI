"""Narrow HTTP client for the isolated coding-runner service."""

import json

import httpx

import config
import stream_clip


def _request(method, path, *, body=None, params=None, timeout=None):
    headers = {}
    if config.CODING_RUNNER_TOKEN:
        headers["x-coding-token"] = config.CODING_RUNNER_TOKEN
    try:
        response = httpx.request(
            method,
            f"{config.CODING_RUNNER_URL}{path}",
            json=body,
            params=params,
            headers=headers,
            timeout=timeout or config.CODING_RUNNER_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        try:
            detail = exc.response.json().get("detail", exc.response.text)
        except Exception:
            detail = exc.response.text
        return {"error": f"coding runner rejected the request: {detail}"}
    except Exception as exc:
        return {"error": f"coding runner unavailable: {exc}"}


# Payload keys that hold captured process output. These are the fields worth clipping
# intelligently; everything else in a runner reply is small metadata that must survive whole.
STREAM_KEYS = ("stdout", "stderr")

# Floor per stream, so a payload with a lot of metadata can never squeeze a stream to nothing —
# a stream clipped to zero reads as "the command printed nothing", which is a different claim.
MIN_STREAM_CHARS = 400


def compact(value, limit=24000):
    """Serialise a runner reply, clipping stream fields (head+tail) before
    re-serialising to preserve metadata and valid JSON.
    Falls back to string truncation only for payloads without stream fields."""
    text = json.dumps(value, ensure_ascii=True)
    if len(text) <= limit:
        return text

    if isinstance(value, dict):
        streams = [k for k in STREAM_KEYS if isinstance(value.get(k), str) and value[k]]
        if streams:
            # Budget = what is left after the non-stream metadata is serialised. Measured by
            # emptying the streams rather than estimated, because an estimate that is wrong in
            # the generous direction just puts us back over the limit.
            skeleton = dict(value)
            for key in streams:
                skeleton[key] = ""
            overhead = len(json.dumps(skeleton, ensure_ascii=True))
            budget = limit - overhead
            if budget > MIN_STREAM_CHARS * len(streams):
                trimmed = dict(value)
                # JSON escaping expands newlines and quotes, so a char budget spent on raw text
                # can still overflow once serialised. Shrink and re-serialise until it fits —
                # bounded, and the loop asserts its own exit rather than trusting one guess.
                per = budget // len(streams)
                for _ in range(6):
                    for key in streams:
                        clipped, was_clipped = stream_clip.clip_text(value[key], per)
                        trimmed[key] = clipped
                        if was_clipped:
                            trimmed[key + "_clipped_for_tool_reply"] = True
                    out = json.dumps(trimmed, ensure_ascii=True)
                    if len(out) <= limit:
                        return out
                    per = max(MIN_STREAM_CHARS, int(per * 0.7))
                # Fell through: keep the tail rather than the head, for the same reason as above.
                return out[-limit:]

    return text[:limit] + f"... [truncated at {limit} characters]"


def workspace(action, workspace=None):
    if action == "list":
        return _request("GET", "/workspaces")
    if action == "open" and workspace:
        return _request("GET", f"/workspaces/{workspace}")
    return {"error": "action must be list or open; open also requires workspace"}


def manifest(workspace):
    return _request("GET", f"/workspaces/{workspace}/manifest")


def read(workspace, path, start_line=1, end_line=None):
    return _request("POST", "/files/read", body={
        "workspace": workspace,
        "path": path,
        "start_line": start_line,
        "end_line": end_line,
    })


def list_files(workspace, path="", depth=3, limit=300):
    return _request("POST", "/files/list", body={
        "workspace": workspace,
        "path": path,
        "depth": depth,
        "limit": limit,
    })


def search(workspace, query, path="", glob=None, limit=100):
    return _request("POST", "/search", body={
        "workspace": workspace,
        "query": query,
        "path": path,
        "glob": glob,
        "limit": limit,
    })


def git(workspace, action, limit=10):
    if action == "status":
        return _request("GET", f"/git/status/{workspace}")
    if action == "diff":
        return _request("GET", f"/git/diff/{workspace}")
    if action == "log":
        return _request("GET", f"/git/log/{workspace}", params={"limit": limit})
    return {"error": "Git action must be status, diff, or log"}


def task_diff(task_id):
    return _request("GET", f"/tasks/{task_id}/diff")


def patch(workspace, path, old, new, expected_sha256, replace_all=False):
    return _request("POST", "/patch", body={
        "workspace": workspace,
        "path": path,
        "old": old,
        "new": new,
        "expected_sha256": expected_sha256,
        "replace_all": replace_all,
    })


def replace(workspace, glob, find, replace, regex=False, max_files=100, dry_run=True,
            files=None, task_id=None):
    body = {"workspace": workspace, "glob": glob, "find": find, "replace": replace,
            "regex": regex, "max_files": max_files, "dry_run": dry_run}
    if files:
        body["files"] = files
    if task_id:
        body["task_id"] = task_id
    return _request("POST", "/files/replace", body=body)


def create(workspace, path, content):
    return _request("POST", "/files/create", body={
        "workspace": workspace,
        "path": path,
        "content": content,
    })


def page_bundle(workspace, path):
    """The page's folder as a tar, for a render on a GPU factory (gpu_render.py)."""
    return _request("POST", "/render/bundle", body={"workspace": workspace, "path": path})


def file_shots(workspace, path, result, pictures):
    """A GPU factory's render filed under renders/ beside the page: -> the result as coding_render reports it."""
    return _request("POST", "/render/shots", body={"workspace": workspace, "path": path, "result": result, "pictures": pictures})


def create_bytes(workspace, path, data):
    """A fetched file written as a FILE (any type), exactly as fetched."""
    import base64
    return _request("POST", "/files/create_bytes", body={
        "workspace": workspace, "path": path, "data_b64": base64.b64encode(data).decode("ascii")})


def ship(workspace, path, *, owner=None, chat_id=None):
    """-> {"path", "shipped_to", "files", "tracked"} or {"error"}: a finished job's folder leaves the workspace for the
    shipping area (the runner's /files/ship; hai_jobs ships a desk job's folder this way when its run ends)."""
    body = {"workspace": workspace, "path": path}
    if owner is not None or chat_id is not None:
        body.update(owner=owner, chat_id=chat_id)
    return _request("POST", "/files/ship", body=body)


def read_bytes(workspace, path):
    """-> {"path", "sha256", "bytes", "data_b64"} or {"error"}: a workspace file's bytes, for a model that can look."""
    return _request("POST", "/files/read_bytes", body={"workspace": workspace, "path": path})


def render(workspace, path, width=None, height=None, steps=None, wait_ms=None, views=None, url=None, query=None):
    """The file as a standard viewer shows it, or a public web page in a real browser (coding-runner render.py). A
    render is a run in the runner, so it is waited for like a test run: past the runner's own limit, never inside it."""
    body = {"workspace": workspace, "path": path or ""}
    for k, v in (("width", width), ("height", height), ("steps", steps), ("wait_ms", wait_ms), ("views", views),
                 ("url", url), ("query", query)):
        if v is not None:
            body[k] = v
    return _request("POST", "/render", body=body, timeout=test_wait())


def move(workspace, source, destination, expected_sha256):
    return _request("POST", "/files/move", body={
        "workspace": workspace,
        "source": source,
        "destination": destination,
        "expected_sha256": expected_sha256,
    })


def delete(workspace, path, expected_sha256):
    return _request("POST", "/files/delete", body={
        "workspace": workspace,
        "path": path,
        "expected_sha256": expected_sha256,
    })


_RUNNER_TEST_S = {"s": None}       # the runner's own test-run limit, as it last reported it


def test_wait():
    """How long to wait for a test run: past the RUNNER's own limit (it times the run), never inside it."""
    learned = _RUNNER_TEST_S["s"]
    return max(config.CODING_TEST_TIMEOUT, (learned + config.CODING_TEST_MARGIN) if learned else 0)


def test(workspace, profile, task_id=None, path=None, worker=None):
    """`path`: run the profile from that folder of the workspace (a job's own folder in a shared scratch workspace)."""
    body = {"workspace": workspace, "profile": profile, "task_id": task_id}
    if path:
        body["path"] = path
    if worker:
        body["worker"] = worker
    result = _request("POST", "/tests/run", body=body, timeout=test_wait())
    if isinstance(result, dict) and isinstance(result.get("timeout_seconds"), (int, float)):
        _RUNNER_TEST_S["s"] = float(result["timeout_seconds"])
    return result


def test_workers():
    """The boxes with a test worker, by name: the runner's list, so there is one."""
    result = _request("GET", "/health")
    return (result.get("test_workers") or []) if isinstance(result, dict) else []


def task_mine(row, user):
    """This person may see and act on this task. A task with no owner predates owners: everyone's, as it always was."""
    return not row.get("owner") or row.get("owner") == user or user in config.ADMIN_USERS


def task_in_this_chat(row, user, chat_id):
    """What a chat is told about without asking: its own tasks. Another chat's are a list away, and another person's are not there. No owner: the admins', who started all of those."""
    if not row.get("owner"):
        return user in config.ADMIN_USERS
    return row.get("owner") == user and row.get("chat_id") == chat_id


def task_door(action, task_id, user, chat_id):
    """The refusal a tool call gets for a task that is not its to touch, or None. `user` None is the lab's own code, which manages every task. Another live chat holding it is
    refused too, or two runs would write in one worktree; once that chat has stopped, its task can be picked up here."""
    if user is None or not task_id or action not in ("get", "update", "finish", "resume", "abandon"):
        return None
    row = task("get", task_id=task_id)
    if not isinstance(row, dict) or row.get("error"):
        return None        # the call itself says what is wrong with the id
    if not task_mine(row, user):
        return "no such task"
    held = row.get("chat_id")
    if action != "get" and row.get("owner") and held and held != chat_id:
        import live_runs
        if live_runs.active(row["owner"], held):
            return "that task is being worked in another chat that is still running; leave it, or stop that run first"
    return None


def task(action, *, workspace=None, objective=None, acceptance_criteria=None, task_id=None,
         plan=None, note=None, status=None, isolate=False, merge=False, include_finished=False, owner=None, chat_id=None):
    # Hidden tasks are invisible to this client but still exist on the server; do not delete or modify them, as parked runs resume when the hide ends.
    import rag
    hidden = rag.hidden_tasks()
    if task_id and task_id in hidden:
        return {"error": "no such task"}
    if action == "list":
        result = _request("GET", "/tasks", params={"include_finished": bool(include_finished)})
        if hidden and isinstance(result, dict) and isinstance(result.get("tasks"), list):
            result = dict(result, tasks=[t for t in result["tasks"] if t.get("id") not in hidden])
        return result
    if action == "start":
        return _request("POST", "/tasks", body={
            "workspace": workspace,
            "objective": objective,
            "acceptance_criteria": acceptance_criteria or [],
            "isolate": bool(isolate),
            "owner": owner,
            "chat_id": chat_id,
        })
    if action == "get" and task_id:
        return _request("GET", f"/tasks/{task_id}")
    if action == "update" and task_id:
        return _request("PATCH", f"/tasks/{task_id}", body={
            "plan": plan,
            "note": note,
            "status": status,
        })
    if action == "finish" and task_id:
        return _request("POST", f"/tasks/{task_id}/finish", body={
            "action": "merge" if merge else "discard",
        })
    if action == "resume" and task_id:
        return _request("POST", f"/tasks/{task_id}/resume", body={"chat_id": chat_id})
    if action == "abandon" and task_id:
        return _request("POST", f"/tasks/{task_id}/abandon")
    return {"error": "task action must be start, get, update, finish, resume, or abandon with the required fields"}


def decision(action, *, task_id=None, decision_id=None, question=None, options=None,
             recommendation=None, rationale="", reversible=True, choice=None, note="",
             include_resolved=False):
    if action == "create":
        return _request("POST", "/decisions", body={
            "task_id": task_id, "question": question, "options": options or [],
            "recommendation": recommendation, "rationale": rationale,
            "reversible": bool(reversible),
        })
    if action == "list":
        return _request("GET", "/decisions", params={"include_resolved": bool(include_resolved)})
    if action == "get" and decision_id:
        return _request("GET", f"/decisions/{decision_id}")
    if action == "resolve" and decision_id:
        return _request("POST", f"/decisions/{decision_id}/resolve",
                        body={"choice": choice, "note": note})
    return {"error": "decision action must be create, list, get, or resolve with required fields"}

def escalation(action, *, task_id=None, packet_id=None, reason=None, requested_help=None,
               suggested_agent=None, agent=None, use_mode=None, outcome=None, note=""):
    if action == "create":
        return _request("POST", "/escalations", body={
            "task_id": task_id, "reason": reason, "requested_help": requested_help,
            "suggested_agent": suggested_agent,
        })
    if action == "list":
        return _request("GET", "/escalations")
    if action == "get" and packet_id:
        return _request("GET", f"/escalations/{packet_id}")
    if action == "select" and packet_id:
        return _request("POST", f"/escalations/{packet_id}/select",
                        body={"agent": agent, "use_mode": use_mode})
    if action == "resolve" and packet_id:
        return _request("POST", f"/escalations/{packet_id}/resolve",
                        body={"outcome": outcome, "note": note})
    return {"error": "escalation action must be create, list, get, select, or resolve with required fields"}

def project_driver(action, *, workspace=None, objective=None, nodes=None, project_id=None,
                   include_finished=False):
    if action == "list":
        return _request("GET", "/projects", params={"include_finished": bool(include_finished)})
    if action == "start":
        result = _request("POST", "/projects", body={
            "workspace": workspace, "objective": objective, "nodes": nodes or [],
        })
        if isinstance(result, dict) and result.get("error") and workspace:
            active = project_driver("list").get("projects", [])
            matches = [item for item in active if item.get("workspace") == workspace
                       and item.get("status") not in ("complete", "cancelled")]
            if len(matches) == 1:
                matches[0]["reused_active"] = True
                return matches[0]
        return result
    if action == "get" and project_id:
        result = _request("GET", f"/projects/{project_id}")
        if isinstance(result, dict) and result.get("error") and workspace:
            active = project_driver("list").get("projects", [])
            matches = [item for item in active if item.get("workspace") == workspace
                       and item.get("status") not in ("complete", "cancelled")]
            if len(matches) == 1:
                matches[0]["recovered_from_project_id"] = project_id
                return matches[0]
        return result
    if action == "advance" and project_id:
        return _request("POST", f"/projects/{project_id}/advance")
    if action == "resume" and project_id:
        return _request("POST", f"/projects/{project_id}/resume")
    return {"error": "project action must be start, get, advance, resume, or list"}

def checkpoint(action, *, task_id, checkpoint_id=None, test_profile=None, note=None):
    if action == "create":
        return _request("POST", "/checkpoints", body={
            "task_id": task_id,
            "test_profile": test_profile,
            "note": note,
        })
    if action == "get" and checkpoint_id:
        return _request("GET", f"/checkpoints/{task_id}/{checkpoint_id}")
    if action == "restore" and checkpoint_id:
        return _request("POST", "/restore", body={"task_id": task_id, "checkpoint_id": checkpoint_id})
    return {"error": "checkpoint action must be create, get, or restore (get/restore require checkpoint_id)"}


def register_workspace(path, id, description="", commands=None, protected_paths=None,
                       ignored_paths=None, shared=True, dry_run=False):
    """Ask the runner to add a workspace. dry_run answers the same validation questions and
    writes nothing, which is what the approval preview needs before an admin decides."""
    return _request("POST", "/workspaces/register", body={
        "id": id, "path": path, "description": description,
        "commands": commands or {}, "protected_paths": protected_paths or [],
        "ignored_paths": ignored_paths or [], "shared": shared, "dry_run": dry_run,
    })


def workspace_source_hashes(workspace):
    """List tracked source files (*.py top-level + tests/*.py) with their sha256 digests.

    Returns ``{"files": [{"rel": str, "sha256": str}, ...]}`` or an error dict on failure."""
    result = _request("POST", "/files/source_hashes", body={"workspace": workspace})
    return result
