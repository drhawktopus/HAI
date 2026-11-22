"""FastAPI adapter for the Home AI coding runner."""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from runner import CodingRunner, ConflictError, PolicyError, RunnerError


BASE = Path(__file__).resolve().parent
runner = CodingRunner(
    os.getenv("CODING_RUNNER_REGISTRY", str(BASE / "registry.json")),
    os.getenv("CODING_RUNNER_STATE", str(BASE / "state")),
)
app = FastAPI(title="Home AI Coding Runner", version="0.1.0")


def _runner_token():
    direct = os.getenv("CODING_RUNNER_TOKEN", "")
    token_file = os.getenv("CODING_RUNNER_TOKEN_FILE", "")
    if direct:
        return direct
    if token_file:
        try:
            return Path(token_file).read_text(encoding="utf-8").strip()
        except OSError:
            return ""
    return ""


RUNNER_TOKEN = _runner_token()


@app.middleware("http")
async def require_runner_token(request: Request, call_next):
    if RUNNER_TOKEN and request.url.path != "/health":
        if request.headers.get("x-coding-token") != RUNNER_TOKEN:
            return JSONResponse(status_code=401, content={"detail": "invalid coding runner token"})
    return await call_next(request)


def call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PolicyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except RunnerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class ListRequest(BaseModel):
    workspace: str
    path: str = ""
    depth: int = 3
    limit: int = 300


class ReadRequest(BaseModel):
    workspace: str
    path: str
    start_line: int = 1
    end_line: int | None = None


class SearchRequest(BaseModel):
    workspace: str
    query: str
    path: str = ""
    glob: str | None = None
    limit: int = 100


class PatchRequest(BaseModel):
    workspace: str
    path: str
    old: str
    new: str
    expected_sha256: str
    replace_all: bool = False


class ReplaceRequest(BaseModel):
    workspace: str
    glob: str
    find: str
    replace: str
    regex: bool = False
    max_files: int
    dry_run: bool = True
    files: list[dict[str, Any]] | None = None
    task_id: str | None = None


class CreateRequest(BaseModel):
    workspace: str
    path: str
    content: str


class CreateBytesRequest(BaseModel):
    workspace: str
    path: str
    data_b64: str


class ReadBytesRequest(BaseModel):
    workspace: str
    path: str


class RenderShotsRequest(BaseModel):
    workspace: str
    path: str
    result: dict
    pictures: dict[str, str]


class RenderRequest(BaseModel):
    workspace: str
    path: str
    width: int = 400
    height: int = 400
    steps: list[dict] | None = None
    wait_ms: int = 1500
    views: list[str] | None = None
    url: str | None = None
    query: str = ""


class MoveRequest(BaseModel):
    workspace: str
    source: str
    destination: str
    expected_sha256: str


class ShipRequest(BaseModel):
    workspace: str
    path: str
    owner: str | None = None
    chat_id: str | None = None


class DeleteRequest(BaseModel):
    workspace: str
    path: str
    expected_sha256: str


class SourceHashesRequest(BaseModel):
    workspace: str


class TestRequest(BaseModel):
    workspace: str
    profile: str
    task_id: str | None = None
    path: str | None = None
    worker: str | None = None       # the box Spock chose for this run, by name (coding_test_box)


class TaskStartRequest(BaseModel):
    workspace: str
    objective: str
    acceptance_criteria: list[str] = Field(default_factory=list)
    isolate: bool = False
    owner: str | None = None
    chat_id: str | None = None


class TaskResumeRequest(BaseModel):
    chat_id: str | None = None


class TaskFinishRequest(BaseModel):
    action: str = "discard"


class TaskUpdateRequest(BaseModel):
    plan: list[dict[str, str]] | None = None
    note: str | None = None
    status: str | None = None


class DecisionCreateRequest(BaseModel):
    task_id: str
    question: str
    options: list[dict[str, str]] = Field(default_factory=list)
    recommendation: str
    rationale: str = ""
    reversible: bool = True


class DecisionResolveRequest(BaseModel):
    choice: str
    note: str = ""

class EscalationCreateRequest(BaseModel):
    task_id: str
    reason: str
    requested_help: str
    suggested_agent: str | None = None

class EscalationSelectRequest(BaseModel):
    agent: str
    use_mode: str


class EscalationResolveRequest(BaseModel):
    outcome: str
    note: str = ""

class ProjectStartRequest(BaseModel):
    workspace: str
    objective: str
    nodes: list[dict[str, Any]] = Field(default_factory=list)

class CheckpointRequest(BaseModel):
    task_id: str
    test_profile: str | None = None
    note: str | None = None


class RestoreRequest(BaseModel):
    task_id: str
    checkpoint_id: str


class RegisterWorkspaceRequest(BaseModel):
    id: str
    path: str
    description: str = ""
    commands: dict[str, list[str]] = Field(default_factory=dict)
    protected_paths: list[str] = Field(default_factory=list)
    ignored_paths: list[str] = Field(default_factory=list)
    shared: bool = True
    dry_run: bool = False


@app.get("/health")
def health():
    return {"ok": True, "workspaces": len(runner.workspaces), "test_workers": sorted(runner.test_workers)}


@app.post("/workspaces/register")
def workspace_register(request: RegisterWorkspaceRequest):
    """Add a workspace to the runtime registry. dry_run answers the same validation questions
    without writing anything, which is what the orchestrator's approval preview needs."""
    return call(
        runner.register_workspace,
        {"id": request.id, "path": request.path, "description": request.description,
         "commands": request.commands, "protected_paths": request.protected_paths,
         "ignored_paths": request.ignored_paths, "shared": request.shared},
        dry_run=request.dry_run,
    )


@app.get("/workspaces")
def workspace_list():
    return {"workspaces": call(runner.workspace_list)}


@app.get("/workspaces/{workspace_id}")
def workspace_open(workspace_id: str):
    return call(runner.workspace_open, workspace_id)


@app.get("/workspaces/{workspace_id}/manifest")
def project_manifest(workspace_id: str):
    return call(runner.project_manifest, workspace_id)


@app.post("/files/list")
def file_list(request: ListRequest):
    return call(
        runner.file_list,
        request.workspace,
        request.path,
        depth=request.depth,
        limit=request.limit,
    )


@app.post("/files/read")
def file_read(request: ReadRequest):
    return call(
        runner.file_read,
        request.workspace,
        request.path,
        start_line=request.start_line,
        end_line=request.end_line,
    )


@app.post("/search")
def text_search(request: SearchRequest):
    return call(
        runner.text_search,
        request.workspace,
        request.query,
        path=request.path,
        glob=request.glob,
        limit=request.limit,
    )


@app.get("/git/status/{workspace_id}")
def git_status(workspace_id: str):
    return call(runner.git_status, workspace_id)


@app.get("/git/diff/{workspace_id}")
def git_diff(workspace_id: str, staged: bool = False):
    return call(runner.git_diff, workspace_id, staged=staged)


@app.get("/git/log/{workspace_id}")
def git_log(workspace_id: str, limit: int = 10):
    return call(runner.git_log, workspace_id, limit=limit)


@app.post("/patch")
def apply_patch(request: PatchRequest):
    return call(
        runner.apply_patch,
        request.workspace,
        request.path,
        old=request.old,
        new=request.new,
        expected_sha256=request.expected_sha256,
        replace_all=request.replace_all,
    )


@app.post("/files/replace")
def replace_files(request: ReplaceRequest):
    """A bulk replace: dry_run previews without writing, apply writes only while every file still
    matches the preview (runner.replace_files)."""
    return call(
        runner.replace_files,
        request.workspace,
        glob=request.glob,
        find=request.find,
        replace=request.replace,
        regex=request.regex,
        max_files=request.max_files,
        dry_run=request.dry_run,
        files=request.files,
        task_id=request.task_id,
    )


@app.post("/files/create")
def file_create(request: CreateRequest):
    return call(
        runner.file_create,
        request.workspace,
        request.path,
        content=request.content,
    )


@app.post("/files/create_bytes")
def file_create_bytes(request: CreateBytesRequest):
    try:
        data = base64.b64decode(request.data_b64, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"data_b64 is not base64: {exc}") from exc
    return call(runner.file_create_bytes, request.workspace, request.path, data=data)


@app.post("/files/read_bytes")
def file_read_bytes(request: ReadBytesRequest):
    result = call(runner.file_read_bytes, request.workspace, request.path)
    result["data_b64"] = base64.b64encode(result.pop("data")).decode("ascii")
    return result


@app.post("/render")
def render_file(request: RenderRequest):
    return call(runner.render, request.workspace, request.path, width=request.width, height=request.height,
                steps=request.steps, wait_ms=request.wait_ms, views=request.views, url=request.url, query=request.query)


@app.post("/render/bundle")
def render_bundle(request: ReadBytesRequest):
    result = call(runner.page_bundle, request.workspace, request.path)
    result["bundle_b64"] = base64.b64encode(result.pop("bundle")).decode("ascii")
    return result


@app.post("/render/shots")
def render_shots(request: RenderShotsRequest):
    try:
        pictures = {name: base64.b64decode(data, validate=True) for name, data in request.pictures.items()}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"a picture is not base64: {exc}") from exc
    return call(runner.file_shots, request.workspace, request.path, result=request.result, pictures=pictures)


@app.post("/files/move")
def file_move(request: MoveRequest):
    return call(
        runner.file_move,
        request.workspace,
        request.source,
        request.destination,
        expected_sha256=request.expected_sha256,
    )


@app.post("/files/delete")
def file_delete(request: DeleteRequest):
    return call(
        runner.file_delete,
        request.workspace,
        request.path,
        expected_sha256=request.expected_sha256,
    )


@app.post("/files/ship")
def file_ship(request: ShipRequest):
    """A finished job's folder leaves the workspace for the shipping area (runner.file_ship)."""
    if request.owner is not None or request.chat_id is not None:
        return call(runner.job_ship, request.workspace, request.path,
                    owner=request.owner, chat_id=request.chat_id)
    return call(runner.file_ship, request.workspace, request.path)


@app.post("/files/source_hashes")
def source_hashes(request: SourceHashesRequest):
    # D0 staleness guard: the orchestrator can't see the workspace tree, so it asks the runner for
    # the workspace's source fingerprint (+ ahead-of-sync-baseline flag) and compares against live.
    return call(runner.source_hashes, request.workspace)


@app.post("/tests/run")
def test_run(request: TestRequest):
    if request.task_id:
        return call(runner.test_run_recover, request.workspace, request.profile, request.task_id, request.path, request.worker)
    return call(runner.test_run, request.workspace, request.profile, request.path, request.worker)


@app.post("/tasks")
def task_start(request: TaskStartRequest):
    return call(
        runner.task_start,
        request.workspace,
        request.objective,
        acceptance_criteria=request.acceptance_criteria,
        isolate=request.isolate,
        owner=request.owner,
        chat_id=request.chat_id,
    )


@app.post("/tasks/{task_id}/finish")
def task_finish(task_id: str, request: TaskFinishRequest):
    return call(runner.task_finish, task_id, action=request.action)


@app.post("/tasks/{task_id}/abandon")
def task_abandon(task_id: str):
    return call(runner.task_abandon, task_id)


@app.post("/tasks/{task_id}/resume")
def task_resume(task_id: str, request: TaskResumeRequest | None = None):
    return call(runner.task_resume, task_id, chat_id=request.chat_id if request else None)


@app.get("/tasks")
def task_list(include_finished: bool = False):
    return call(runner.task_list, include_finished=include_finished)


@app.get("/tasks/{task_id}")
def task_get(task_id: str):
    return call(runner.task_get, task_id)


@app.get("/tasks/{task_id}/diff")
def task_branch_diff(task_id: str):
    return call(runner.task_branch_diff, task_id)


@app.patch("/tasks/{task_id}")
def task_update(task_id: str, request: TaskUpdateRequest):
    return call(
        runner.task_update,
        task_id,
        plan=request.plan,
        note=request.note,
        status=request.status,
    )


@app.post("/decisions")
def decision_create(request: DecisionCreateRequest):
    return call(runner.decision_create, request.task_id, request.question, request.options,
                request.recommendation, rationale=request.rationale, reversible=request.reversible)


@app.get("/decisions")
def decision_list(include_resolved: bool = False):
    return call(runner.decision_list, include_resolved=include_resolved)


@app.get("/decisions/{decision_id}")
def decision_get(decision_id: str):
    return call(runner.decision_get, decision_id)


@app.post("/decisions/{decision_id}/resolve")
def decision_resolve(decision_id: str, request: DecisionResolveRequest):
    return call(runner.decision_resolve, decision_id, request.choice, note=request.note)

@app.post("/escalations")
def escalation_create(request: EscalationCreateRequest):
    return call(runner.escalation_packet_create, request.task_id, request.reason,
                request.requested_help, suggested_agent=request.suggested_agent)


@app.get("/escalations")
def escalation_list():
    return call(runner.escalation_packet_list)


@app.get("/escalations/{packet_id}")
def escalation_get(packet_id: str):
    return call(runner.escalation_packet_get, packet_id)

@app.post("/escalations/{packet_id}/select")
def escalation_select(packet_id: str, request: EscalationSelectRequest):
    return call(runner.escalation_packet_select, packet_id, request.agent, request.use_mode)

@app.post("/escalations/{packet_id}/resolve")
def escalation_resolve(packet_id: str, request: EscalationResolveRequest):
    return call(runner.escalation_packet_resolve, packet_id, request.outcome, note=request.note)

@app.post("/projects")
def project_start(request: ProjectStartRequest):
    return call(runner.project_start, request.workspace, request.objective, request.nodes)


@app.get("/projects")
def project_list(include_finished: bool = False):
    return call(runner.project_list, include_finished=include_finished)


@app.get("/projects/{project_id}")
def project_get(project_id: str):
    return call(runner.project_get, project_id)


@app.post("/projects/{project_id}/advance")
def project_advance(project_id: str):
    return call(runner.project_advance, project_id)


@app.post("/projects/{project_id}/resume")
def project_resume(project_id: str):
    return call(runner.project_resume, project_id)

@app.post("/checkpoints")
def task_checkpoint(request: CheckpointRequest):
    return call(
        runner.task_checkpoint,
        request.task_id,
        test_profile=request.test_profile,
        note=request.note,
    )


@app.get("/checkpoints/{task_id}/{checkpoint_id}")
def task_checkpoint_get(task_id: str, checkpoint_id: str):
    return call(runner.task_checkpoint_get, task_id, checkpoint_id)


@app.post("/restore")
def task_restore(request: RestoreRequest):
    return call(runner.task_restore, request.task_id, request.checkpoint_id)
