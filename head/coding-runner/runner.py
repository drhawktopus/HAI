"""Policy-enforced workspace operations for the Home AI coding runner."""

from __future__ import annotations

import hashlib
import json
import os

_OWNER = os.environ.get("OWNER_USER", "").strip() or "the admin"   # who approves (a setting)
import re
import subprocess
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Shared with the orchestrator's coding_client.compact — ONE clipping derivation across both
# truncation layers on this path. Copied into the image by the Dockerfile (the build context is
# ./orchestrator, so the orchestrator-level module is visible from here).
import stream_clip


class RunnerError(RuntimeError):
    """Base error returned to the API without exposing a traceback."""


class PolicyError(RunnerError):
    """The requested operation is outside the registered workspace policy."""


class ConflictError(RunnerError):
    """The workspace changed since the caller inspected it."""


# A missing hash is not a changed file: told "file changed", a model re-reads and retries the same call.
NO_SHA = "expected_sha256 is required - pass the sha256 that coding_read returned for this file"


@dataclass(frozen=True)
class Workspace:
    id: str
    root: Path
    description: str
    read: bool
    write: bool
    git: bool
    protected_paths: tuple[str, ...]
    ignored_paths: tuple[str, ...]
    commands: dict[str, tuple[str, ...]]
    # Several jobs at once on one repository: every task gets its own worktree whatever it asked
    # for, a merge combines with whatever landed since the task started, and its tests may run on
    # another box. A workspace without it takes one task at a time and fast-forward merges. A
    # workspace registered at run time is shared by default.
    shared: bool = False
    # True for a workspace added through /workspaces/register rather than baked into the image:
    # its folder belongs to the host, so git needs its trust on every runner call.
    local: bool = False

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "read": self.read,
            "write": self.write,
            "git": self.git,
            "protected_paths": list(self.protected_paths),
            "ignored_paths": list(self.ignored_paths),
            "test_profiles": sorted(self.commands),
            "shared": self.shared,
            "registered_at_runtime": self.local,
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


# A fetched or looked-at file's size limit: the orchestrator's web door stops a download at 32 MiB
# (config.WEB_FETCH_MAX_DOWNLOAD), so anything it can fetch can be written here, and nothing larger.
BYTES_MAX = 32 * 1024 * 1024
PAGE_TEXT_TYPES = (".html", ".htm", ".svg", ".js", ".mjs", ".css", ".json")   # where a page's own `../` can be written
PAGE_TEXT_MAX = 8 * 1024 * 1024

# Bounds for a bulk replace: how many files one call may touch, and how much of the preview the
# caller is shown. Module level because the preview builder is a static method.
REPLACE_MAX_FILES = 400
REPLACE_SAMPLE_LIMIT = 3
REPLACE_SAMPLE_CHARS = 200


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# Identity used only for local sandbox checkpoint commits (never pushed). Passed per-command
# via `-c` so no global/repo git config is mutated.
GIT_IDENTITY = [
    "-c", "user.name=Home AI Coding Runner",
    "-c", "user.email=coding-runner@homeai.local",
]
_SHA_RE = re.compile(r"[0-9a-f]{7,40}")


class CodingRunner:
    # A workspace added at run time always protects at least these; the caller's list is added on
    # top of them, never replacing them.
    DEFAULT_PROTECTED = (".git", ".env", "secrets", "backups")
    # Where a registered workspace may live (env CODING_RUNNER_SHARE_ROOTS overrides).
    DEFAULT_SHARE_ROOTS = "/app/shares"
    # The runtime registry sits beside the baked one and survives a container recreate.
    DEFAULT_LOCAL_REGISTRY = "/state/registry.local.json"

    def __init__(
        self,
        registry_path: str | Path,
        state_dir: str | Path,
        *,
        # Two separate bounds for distinct safety concerns: `max_read_slice_bytes` limits the payload
        # returned to the model (protecting context window), while edit operations rely on sha-anchored
        # patches and range reads rather than file-size caps. Do not recombine or remove these.
        max_read_slice_bytes: int = 256_000,   # max_edit_file_bytes deliberately absent: file-size gates were removed
# because safety is handled by size-blind mechanisms (sha-anchored patches,
# range reads, sandboxing). Do not reintroduce a size limit.
        max_output_bytes: int = 128_000,
    ):
        self.registry_path = Path(registry_path).resolve()
        self.state_dir = Path(state_dir).resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.tasks_dir = self.state_dir / "tasks"
        self.tasks_dir.mkdir(exist_ok=True)
        self.checkpoints_dir = self.state_dir / "checkpoints"
        self.checkpoints_dir.mkdir(exist_ok=True)
        self.projects_dir = self.state_dir / "projects"
        self.projects_dir.mkdir(exist_ok=True)
        self.decisions_dir = self.state_dir / "decisions"
        self.decisions_dir.mkdir(exist_ok=True)
        self.escalations_dir = self.state_dir / "escalations"
        self.escalations_dir.mkdir(exist_ok=True)
        self.audit_path = self.state_dir / "audit.jsonl"
        self.max_read_slice_bytes = max_read_slice_bytes
        self.max_output_bytes = max_output_bytes
        self.task_auto_cancel_seconds = int(os.getenv("TASK_AUTO_CANCEL_SECONDS", "86400"))
        # The full workspace suite runs ~30-60s today and only grows; a tight fixed cap (was 60)
        # would eventually time out a slow-but-PASSING suite, giving HAI no green result ever. Keep
        # generous headroom, still bounded so a genuine hang returns. Env-overridable, not a magic literal.
        self.test_run_timeout_seconds = int(os.getenv("CODING_TEST_TIMEOUT_SECONDS", "300"))
        # A run that keeps allocating is ended before the kernel kills it: past this much memory a run gets a stack dump of
        # the line it is on, then stops, so the reply says WHERE it grew instead of only returncode -9 (which reads like the
        # time limit). A judgment figure, not a measurement: far above what a job's tests need, and well below what
        # endangers the VM that also holds Kirk.
        self.test_memory_gb = float(os.getenv("CODING_TEST_MEMORY_GB", "8"))
        # Off unless set: ending a run on no CPU and no output is a policy change, and it is opt-in.
        self.test_idle_seconds = int(os.getenv("CODING_TEST_IDLE_SECONDS", "0"))
        self.test_poll_seconds = 1.0
        # A job's tests can run on another box. Off unless set: the runner runs them here, as it always has.
        # "The Tolkien box=http://host:8097,the lead=http://host:8097": the boxes that have one. Which of them takes a run is Spock's call, made on Kirk's side (coding_test_box).
        self.test_workers = {name.strip(): url.strip().rstrip("/") for name, _, url in
                             (part.partition("=") for part in os.getenv("CODING_TEST_WORKERS", "").split(",")) if name.strip() and url.strip()}
        token_file = os.getenv("CODING_RUNNER_TOKEN_FILE", "")
        self.test_worker_token = Path(token_file).read_text(encoding="utf-8").strip() if token_file and os.path.exists(token_file) else ""
        self.test_worker_answer_seconds = 5
        self._audit_lock = threading.Lock()
        self._merge_lock = threading.Lock()    # two jobs finishing together must not merge into one working tree at the same moment
        self.local_registry_path = Path(os.getenv("CODING_RUNNER_LOCAL_REGISTRY", self.DEFAULT_LOCAL_REGISTRY))
        self.baked_ids = set()
        self.shadowed_ids: list[str] = []
        self.workspaces = self._load_registry()
        self.baked_ids = set(self.workspaces)
        self._load_local_registry()
        self._reattach_worktrees()

    def _share_roots(self) -> list[Path]:
        """The only folders a registered workspace may live in (env CODING_RUNNER_SHARE_ROOTS)."""
        raw = os.getenv("CODING_RUNNER_SHARE_ROOTS", self.DEFAULT_SHARE_ROOTS)
        roots = []
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                roots.append(Path(part).resolve(strict=True))
            except OSError:
                roots.append(Path(part).resolve())
        return roots

    def _load_local_registry(self) -> None:
        """Read the workspaces added by approval, beside the baked set. A baked id always wins:
        a runtime entry with that id is reported as shadowed, never loaded."""
        try:
            raw = json.loads(self.local_registry_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return                                     # no file yet, or unreadable: nothing added yet
        for item in raw.get("workspaces", []):
            try:
                workspace = self._workspace_from_entry(item)
            except PolicyError as exc:
                self.shadowed_ids.append(f"{item.get('id')}: {exc}")
                continue
            if workspace.id in self.baked_ids:
                self.shadowed_ids.append(workspace.id)
                continue
            self.workspaces[workspace.id] = workspace

    def _workspace_from_entry(self, item: dict[str, Any]) -> Workspace:
        workspace_id = item["id"]
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", workspace_id):
            raise PolicyError(f"invalid workspace id: {workspace_id!r}")
        root = Path(item["path"]).resolve()
        if not root.is_dir():
            raise PolicyError(f"workspace root does not exist: {root}")
        commands = {name: tuple(str(part) for part in argv) for name, argv in item.get("commands", {}).items()}
        for name, argv in commands.items():
            reason = self._command_reason(name, list(argv), root)
            if reason:
                raise PolicyError(f"command {name!r} is not an allowed template: {reason}")
        return Workspace(
            id=workspace_id,
            root=root,
            description=item.get("description", ""),
            read=bool(item.get("read", True)),
            write=bool(item.get("write", False)),
            git=bool(item.get("git", False)),
            protected_paths=tuple(item.get("protected_paths", [])),
            ignored_paths=tuple(item.get("ignored_paths", [".git"])),
            commands=commands,
            shared=bool(item.get("shared", False)),
            local=bool(item.get("local", False)),
        )

    def _command_reason(self, name: str, argv: list[str], root: Path) -> str | None:
        """None when argv matches an allowed template, else why it does not.

        Only three shapes are allowed: unittest discover with any subset of -s/-t/-p/-v,
        compileall -q ., and a plain `python [warnings flag] <file>.py` inside the workspace.
        An optional `env NAME=VALUE ...` prefix is permitted on any of them. There is no
        wildcard profile: every command a workspace can run is spelled out at registration.
        """
        parts = list(argv)
        if not parts:
            return "the command is empty"
        if parts[0] == "env":
            parts = parts[1:]
            while parts and re.fullmatch(r"[A-Z_]+=[^\s]*", parts[0]):
                parts = parts[1:]
            if not parts:
                return "the env prefix has no command after it"
        if parts == ["python", "-m", "compileall", "-q", "."]:
            return None
        if parts[:4] == ["python", "-m", "unittest", "discover"]:
            rest = parts[4:]
            if not any(flag in ("-s", "-t", "-p") for flag in rest):
                return ("unittest discover needs at least one of -s, -t or -p; a bare discover "
                        "would run everything in the folder, including scripts that act when imported")
            i = 0
            while i < len(rest):
                flag = rest[i]
                if flag == "-v":
                    i += 1
                    continue
                if flag in ("-s", "-t", "-p"):
                    if i + 1 >= len(rest) or not str(rest[i + 1]).strip():
                        return f"{flag} needs a value"
                    i += 2
                    continue
                return f"{flag!r} is not one of -s, -t, -p or -v"
            return None
        if parts[0] == "python":
            if parts[1:3] == ["-W", "error::SyntaxWarning"]:
                parts = parts[:1] + parts[3:]
            if len(parts) == 2 and parts[1].endswith(".py"):
                target = Path(parts[1])
                if target.is_absolute() or any(part == ".." for part in target.parts):
                    return "the script must be a relative path inside the workspace"
                try:
                    resolved = (root / target).resolve(strict=True)
                    resolved.relative_to(root)
                except (OSError, ValueError):
                    return f"the script {parts[1]!r} is not a file inside the workspace"
                return None
            return "python may run a script file or unittest discover, not -c or a flag"
        return f"{parts[0]!r} is not an allowed command; use python, unittest discover, compileall, or a python script in the workspace"

    def register_workspace(self, payload: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
        """Validate and (unless dry_run) record a new workspace. Nothing is written unless every
        check passes. Returns the entry plus what the preview needs to say honestly."""
        workspace_id = str(payload.get("id", "")).strip()
        if not re.fullmatch(r"[a-z0-9-]{3,40}", workspace_id):
            raise PolicyError("workspace id must be 3-40 characters of a-z, 0-9 and '-'")
        if workspace_id in self.baked_ids:
            raise PolicyError(f"workspace id {workspace_id!r} is already in the baked registry")
        if workspace_id in self.workspaces:
            raise PolicyError(f"workspace id {workspace_id!r} already exists")

        configured = str(payload.get("path", "")).strip()
        if not configured:
            raise PolicyError("path is required")
        candidate = Path(configured)
        if not candidate.is_absolute():
            raise PolicyError("the workspace path must be absolute")
        try:
            root = candidate.resolve(strict=True)
        except OSError:
            raise PolicyError(f"the path does not exist: {configured}")
        roots = self._share_roots()
        inside = False
        for share in roots:
            try:
                root.relative_to(share)
                inside = True
                break
            except ValueError:
                continue
        if not inside:
            raise PolicyError(
                "the path is not under an allowed share root (%s)" % ", ".join(str(r) for r in roots)
            )
        if not (root / ".git").exists():
            raise PolicyError(f"{root} is not a Git repository; a workspace needs local Git history")

        protected = list(dict.fromkeys([*self.DEFAULT_PROTECTED,
                                        *(str(p) for p in payload.get("protected_paths", []) if str(p).strip())]))
        commands: dict[str, tuple[str, ...]] = {}
        rejected: list[str] = []
        for name, argv in (payload.get("commands") or {}).items():
            argv = [str(part) for part in argv]
            reason = self._command_reason(name, argv, root)
            if reason:
                rejected.append(f"{name}: {reason}")
            else:
                commands[name] = tuple(argv)
        if rejected:
            raise PolicyError("command not allowed - " + "; ".join(rejected))

        remotes = self._run(["git", "-C", str(root), "-c", "safe.directory=%s" % root, "remote"], root, timeout=10)
        if remotes["returncode"] != 0:
            raise PolicyError("git cannot read this repository: %s" % (remotes["stderr"] or "").strip()[:200])
        remote_lines = [line.strip() for line in (remotes["stdout"] or "").splitlines() if line.strip()]
        if remote_lines:
            raise PolicyError("this repository has Git remotes (%s); workspaces must stay local-only"
                              % ", ".join(remote_lines[:3]))

        entry = {
            "id": workspace_id,
            "path": str(root),
            "description": str(payload.get("description", "")).strip(),
            "read": bool(payload.get("read", True)),
            "write": bool(payload.get("write", True)),
            "git": True,
            "shared": bool(payload.get("shared", True)),
            "local": True,
            "protected_paths": protected,
            "ignored_paths": list(dict.fromkeys([*(payload.get("ignored_paths") or [".git"]), ".git"])),
            "commands": {name: list(argv) for name, argv in commands.items()},
        }
        if not dry_run:
            self._write_local_registry(entry)
            self.workspaces[workspace_id] = self._workspace_from_entry(entry)
            self._audit("workspace_register", workspace_id, {"path": str(root), "profiles": sorted(commands)})
        return {
            "registered": not dry_run,
            "workspace": entry,
            "git_trusted": True,
            "test_profiles": sorted(commands),
            "proposed_not_run": sorted(commands),
            "note": ("Each profile was checked against the allowed command templates, not executed. "
                     "The first coding_test on this workspace is the first real run of them."),
        }

    def _write_local_registry(self, entry: dict[str, Any]) -> None:
        """Append one entry to the runtime registry and reload, with no restart. Atomic write:
        the file is replaced by a complete copy, so a failure mid-write leaves the previous file."""
        existing = []
        try:
            existing = json.loads(self.local_registry_path.read_text(encoding="utf-8")).get("workspaces", [])
        except (OSError, ValueError):
            existing = []
        payload = {"workspaces": [*existing, entry]}
        self.local_registry_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.local_registry_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.local_registry_path)

    def _reattach_worktrees(self) -> None:
        """A restart must not cost a live job its files. A task's isolated worktree is registered in memory only, so
        after a restart every call naming it would answer "unknown workspace" until someone called task_resume, and a
        resumed job would rebuild its files in the base workspace. Every unfinished task's worktree that is still on disk
        is registered again here, at startup.
        NEVER raises: a task record that does not read is skipped, the runner still starts."""
        for path in self.tasks_dir.glob("*.json"):
            try:
                task = json.loads(path.read_text(encoding="utf-8"))
                worktree = task.get("worktree")
                if not isinstance(worktree, dict) or task.get("worktree_finished"):
                    continue
                wt_id, wt_root = worktree["workspace"], Path(worktree["path"])
                base = self.workspaces.get(worktree["base"])
                if wt_id in self.workspaces or base is None or not wt_root.is_dir() or not (wt_root / ".git").exists():
                    continue
                self._register_worktree_workspace(base, wt_id, wt_root.resolve(), worktree["branch"], task.get("id") or path.stem)
            except Exception:                                            # noqa: BLE001 - startup must not fail on one record
                continue

    def _load_registry(self) -> dict[str, Workspace]:
        raw = json.loads(self.registry_path.read_text(encoding="utf-8"))
        base = self.registry_path.parent
        workspaces: dict[str, Workspace] = {}
        for item in raw.get("workspaces", []):
            workspace_id = item["id"]
            if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", workspace_id):
                raise PolicyError(f"invalid workspace id: {workspace_id!r}")
            configured = Path(os.path.expandvars(item["path"]))
            root = (base / configured).resolve() if not configured.is_absolute() else configured.resolve()
            if not root.is_dir():
                raise PolicyError(f"workspace root does not exist: {root}")
            commands = {
                name: tuple(str(part) for part in argv)
                for name, argv in item.get("commands", {}).items()
            }
            workspace = Workspace(
                id=workspace_id,
                root=root,
                description=item.get("description", ""),
                read=bool(item.get("read", True)),
                write=bool(item.get("write", False)),
                git=bool(item.get("git", False)),
                protected_paths=tuple(item.get("protected_paths", [])),
                ignored_paths=tuple(item.get("ignored_paths", [".git"])),
                commands=commands,
                shared=bool(item.get("shared", False)),
            )
            self._assert_local_git_only(workspace)
            workspaces[workspace_id] = workspace
        if not workspaces:
            raise PolicyError("registry contains no workspaces")
        return workspaces

    def _assert_local_git_only(self, workspace: Workspace) -> None:
        if not workspace.git or not (workspace.root / ".git").exists():
            return
        result = self._run(
            ["git", "-c", "safe.directory=%s" % workspace.root,
             "-C", str(workspace.root), "remote"],
            workspace.root,
            timeout=5,
        )
        if result["returncode"] != 0:
            raise PolicyError(f"cannot inspect Git remotes for workspace: {workspace.id}")
        remotes = [line.strip() for line in result["stdout"].splitlines() if line.strip()]
        if remotes:
            raise PolicyError(
                f"workspace {workspace.id} has Git remotes configured; "
                "Home AI repositories must remain local-only"
            )

    def _audit(self, action: str, workspace: str | None, detail: dict[str, Any]) -> None:
        record = {
            "timestamp": _utc_now(),
            "action": action,
            "workspace": workspace,
            "detail": detail,
        }
        line = json.dumps(record, ensure_ascii=True, separators=(",", ":")) + "\n"
        with self._audit_lock:
            with self.audit_path.open("a", encoding="utf-8") as handle:
                handle.write(line)

    def _workspace(self, workspace_id: str, *, write: bool = False) -> Workspace:
        workspace = self.workspaces.get(workspace_id)
        if workspace is None:
            raise PolicyError(f"unknown workspace: {workspace_id}")
        if write and not workspace.write:
            raise PolicyError(f"workspace is read-only: {workspace_id}")
        if not write and not workspace.read:
            raise PolicyError(f"workspace is not readable: {workspace_id}")
        return workspace

    @staticmethod
    def _relative_text(path: str) -> str:
        if not isinstance(path, str) or not path.strip():
            raise PolicyError("path must be a non-empty relative string")
        if "\x00" in path or ":" in path:
            raise PolicyError("path contains a forbidden character")
        candidate = Path(path)
        if (
            candidate.is_absolute()
            or path.startswith(("/", "\\"))
            or any(part in ("", ".", "..") for part in candidate.parts)
        ):
            raise PolicyError("path must be normalized and relative to the workspace")
        return candidate.as_posix()

    def _resolve(
        self,
        workspace: Workspace,
        relative: str,
        *,
        write: bool = False,
        allow_missing: bool = False,
    ) -> tuple[Path, str]:
        rel = self._relative_text(relative)
        protected = set(workspace.protected_paths)
        if write and any(rel == prefix or rel.startswith(prefix.rstrip("/") + "/") for prefix in protected):
            raise PolicyError(f"path is protected: {rel}")

        lexical = workspace.root.joinpath(*Path(rel).parts)
        try:
            if allow_missing and not lexical.exists():
                # Anchor on the deepest existing ancestor, not immediate parent,
                # to allow creating paths in non-existent intermediate directories.
                # rel is normalized (no '..'); symlink escape in existing ancestors
                # is caught by strict resolution and workspace containment checks below.
                anchor = lexical.parent
                while not anchor.exists() and anchor != workspace.root:
                    anchor = anchor.parent
                resolved = anchor.resolve(strict=True) / lexical.relative_to(anchor)
            else:
                resolved = lexical.resolve(strict=True)
        except FileNotFoundError as exc:
            raise PolicyError(f"path does not exist: {rel}") from exc

        try:
            resolved.relative_to(workspace.root)
        except ValueError as exc:
            raise PolicyError("resolved path escapes the workspace") from exc
        return resolved, rel

    def workspace_list(self) -> list[dict[str, Any]]:
        result = [workspace.public() for workspace in self.workspaces.values()]
        self._audit("workspace_list", None, {"count": len(result)})
        return result

    def workspace_open(self, workspace_id: str) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        result = workspace.public()
        result["git_repository"] = (workspace.root / ".git").exists()
        self._audit("workspace_open", workspace_id, {})
        return result

    def project_manifest(self, workspace_id: str) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        candidates = {
            "instructions": ["AGENTS.md", "CLAUDE.md", "CONTRIBUTING.md"],
            "python": ["pyproject.toml", "requirements.txt", "setup.py", "setup.cfg"],
            "javascript": ["package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock"],
            "containers": ["Dockerfile", "docker-compose.yml", "docker-compose.yaml"],
            "other": ["Makefile", "README.md"],
        }
        found: dict[str, list[str]] = {}
        for category, names in candidates.items():
            present = [
                name for name in names
                if (workspace.root / name).is_file() and not self._is_ignored(workspace, name)
            ]
            if present:
                found[category] = present
        branch = None
        if workspace.git and (workspace.root / ".git").exists():
            result = self._git(workspace, ["branch", "--show-current"])
            branch = result["stdout"].strip() or None
        manifest = {
            "workspace": workspace.public(),
            "git_branch": branch,
            "detected_files": found,
            "test_profiles": {
                name: list(argv) for name, argv in sorted(workspace.commands.items())
            },
        }
        self._audit("project_manifest", workspace_id, {"detected_files": found})
        return manifest

    @staticmethod
    def _is_ignored(workspace: Workspace, relative: str) -> bool:
        return any(
            relative == prefix or relative.startswith(prefix.rstrip("/") + "/")
            for prefix in workspace.ignored_paths
        )

    def file_list(self, workspace_id: str, path: str = "", *, depth: int = 3, limit: int = 300) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        if not path:
            base, base_rel = workspace.root, ""
        else:
            base, base_rel = self._resolve(workspace, path)
        if not base.is_dir():
            raise PolicyError("file_list path must be a directory")
        depth = max(0, min(depth, 8))
        limit = max(1, min(limit, 1_000))
        entries: list[dict[str, Any]] = []
        for current, dirs, files in os.walk(base, followlinks=False):
            current_path = Path(current)
            current_rel = current_path.relative_to(workspace.root).as_posix()
            current_depth = len(current_path.relative_to(base).parts)
            dirs[:] = sorted(
                d
                for d in dirs
                if not self._is_ignored(
                    workspace,
                    (current_path / d).relative_to(workspace.root).as_posix(),
                )
            )
            if current_depth >= depth:
                dirs[:] = []
            for name in sorted(dirs):
                rel = (current_path / name).relative_to(workspace.root).as_posix()
                entries.append({"path": rel, "type": "directory"})
                if len(entries) >= limit:
                    break
            if len(entries) >= limit:
                break
            for name in sorted(files):
                item = current_path / name
                rel = item.relative_to(workspace.root).as_posix()
                if self._is_ignored(workspace, rel):
                    continue
                entries.append({"path": rel, "type": "file", "size": item.stat().st_size})
                if len(entries) >= limit:
                    break
            if len(entries) >= limit:
                break
        self._audit("file_list", workspace_id, {"path": base_rel, "count": len(entries)})
        return {"entries": entries, "truncated": len(entries) >= limit}

    def file_read(
        self,
        workspace_id: str,
        path: str,
        *,
        start_line: int = 1,
        end_line: int | None = None,
    ) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        target, rel = self._resolve(workspace, path)
        if not target.is_file():
            raise PolicyError("file_read path must be a file")
        data = target.read_bytes()
        if b"\x00" in data[:8_192]:
            raise PolicyError("binary files are not readable")
        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()
        start = max(1, start_line)
        end = len(lines) if end_line is None else min(len(lines), max(start, end_line))
        selected = lines[start - 1 : end]

        # BOUND THE SLICE, NOT THE FILE. The old check ran on `data` BEFORE this slice, so a 23-line
        # range read of a large file was refused for the size of bytes it was never going to return.
        # A read bound exists to protect the model's context; the only thing that reaches the model is
        # `selected`, so `selected` is what must be measured.
        #
        # The refusal is LOUD and ACTIONABLE, and that is not decoration: measured this session, a
        # named failure ("No document matched ... your library: ...") taught HAI the filename
        # convention on its very next call, where an empty result would have taught it nothing. So an
        # over-bound read states the bound, what was asked for, and a range that WOULD fit.
        payload = "\n".join(selected)
        payload_bytes = len(payload.encode("utf-8"))
        if payload_bytes > self.max_read_slice_bytes:
            span = max(1, end - start + 1)
            fits = max(1, int(span * self.max_read_slice_bytes / payload_bytes * 0.9))
            raise PolicyError(
                f"requested range is {payload_bytes} bytes, over the {self.max_read_slice_bytes}-byte "
                f"read-slice limit. You asked for lines {start}-{end} ({span} lines) of "
                f"{len(lines)}. Retry with a narrower range — about {fits} lines fits, "
                f"e.g. start_line={start}, end_line={start + fits - 1}."
            )
        self._audit("file_read", workspace_id, {"path": rel, "start": start, "end": end})
        return {
            "path": rel,
            "start_line": start,
            "end_line": end,
            "total_lines": len(lines),
            "sha256": _sha256(data),
            "content": "\n".join(selected),
        }

    def text_search(
        self,
        workspace_id: str,
        query: str,
        *,
        path: str = "",
        glob: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        if not query or len(query) > 500:
            raise PolicyError("query must contain 1-500 characters")
        base = workspace.root
        if path:
            base, _ = self._resolve(workspace, path)
        limit = max(1, min(limit, 500))
        argv = ["rg", "--json", "--line-number", "--color", "never"]
        for ignored in workspace.ignored_paths:
            argv.extend(["--glob", f"!{ignored.rstrip('/')}/**"])
        if glob:
            if "\x00" in glob or glob.startswith("/") or ":" in glob or ".." in Path(glob).parts:
                raise PolicyError("invalid glob")
            argv.extend(["--glob", glob])
        argv.extend(["--", query, str(base)])
        result = self._run(argv, workspace.root, timeout=10)
        matches: list[dict[str, Any]] = []
        for line in result["stdout"].splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") != "match":
                continue
            data = event["data"]
            absolute = Path(data["path"]["text"]).resolve()
            try:
                rel = absolute.relative_to(workspace.root).as_posix()
            except ValueError:
                continue
            matches.append(
                {
                    "path": rel,
                    "line": data["line_number"],
                    "text": data["lines"]["text"].rstrip("\r\n"),
                }
            )
            if len(matches) >= limit:
                break
        self._audit("text_search", workspace_id, {"query": query, "count": len(matches)})
        return {"matches": matches, "truncated": len(matches) >= limit}

    def _git(self, workspace: Workspace, args: list[str], timeout: int = 15) -> dict[str, Any]:
        if not workspace.git:
            raise PolicyError("Git operations are disabled for this workspace")
        if not (workspace.root / ".git").exists():
            raise PolicyError("workspace is not a Git repository")
        argv = ["git"]
        if workspace.local or (workspace.id.startswith("wt-") and workspace.root.parent.name == ".worktrees"):
            # The runner runs as root over repositories owned by the host. Git refuses every
            # command in a folder it does not trust ("dubious ownership") and answers nothing,
            # which reads as a clean tree. The container's global git config does not survive a
            # recreate, so the trust is stated on every call instead of written once.
            argv += ["-c", "safe.directory=%s" % workspace.root]
        result = self._run([*argv, "-C", str(workspace.root), *args], workspace.root, timeout=timeout)
        err = result.get("stderr") or ""
        if result.get("returncode") == 128 and ("dubious ownership" in err or "not a git repository" in err):
            # git could not work in this repository at all: "could not ask" is never an empty answer
            raise ConflictError("git cannot work in workspace %s (%s); nothing was changed" % (
                workspace.id, (err.strip().splitlines() or ["no message"])[0][:200]))
        return result

    def git_status(self, workspace_id: str) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        result = self._git(workspace, ["status", "--short", "--branch"])
        self._audit("git_status", workspace_id, {})
        return result

    def git_diff(self, workspace_id: str, *, staged: bool = False) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        args = ["diff", "--no-ext-diff", "--no-color"]
        if staged:
            args.append("--cached")
        result = self._git(workspace, args)
        included: list[str] = []
        skipped: list[str] = []
        if not staged:
            untracked = self._git(
                workspace,
                ["ls-files", "--others", "--exclude-standard", "-z"],
            )
            additions: list[str] = []
            for rel in untracked["stdout"].split("\x00"):
                if not rel:
                    continue
                if self._is_ignored(workspace, rel) or any(
                    rel == prefix or rel.startswith(prefix.rstrip("/") + "/")
                    for prefix in workspace.protected_paths
                ):
                    skipped.append(rel)
                    continue
                try:
                    target, normalized = self._resolve(workspace, rel)
                    data = target.read_bytes()
                    # Untracked-file diff bodies emit a WHOLE file as text — a PAYLOAD concern
                    # (the reviewer's context), so it rides the read-slice bound. This is NOT the
                    # removed edit policy returning: same numeric behaviour as before the removal,
                    # different (and legitimate) reason.
                    if len(data) > self.max_read_slice_bytes or b"\x00" in data[:8_192]:
                        skipped.append(normalized)
                        continue
                    text = data.decode("utf-8")
                except (OSError, UnicodeDecodeError, PolicyError):
                    skipped.append(rel)
                    continue
                body = "".join(f"+{line}\n" for line in text.splitlines())
                if text and not text.endswith(("\n", "\r")):
                    body += "\\ No newline at end of file\n"
                additions.append(
                    f"diff --git a/{normalized} b/{normalized}\n"
                    "new file mode 100644\n"
                    "--- /dev/null\n"
                    f"+++ b/{normalized}\n"
                    f"@@ -0,0 +1,{len(text.splitlines())} @@\n"
                    f"{body}"
                )
                included.append(normalized)
            if additions:
                separator = "" if not result["stdout"] or result["stdout"].endswith("\n") else "\n"
                result["stdout"] += separator + "".join(additions)
                if len(result["stdout"].encode("utf-8")) > self.max_output_bytes:
                    encoded = result["stdout"].encode("utf-8")[: self.max_output_bytes]
                    result["stdout"] = encoded.decode("utf-8", errors="ignore")
                    result["stdout_truncated"] = True
        result["untracked_included"] = included
        result["untracked_skipped"] = skipped
        self._audit(
            "git_diff",
            workspace_id,
            {"staged": staged, "untracked_included": included, "untracked_skipped": skipped},
        )
        return result

    def task_branch_diff(self, task_id: str) -> dict[str, Any]:
        """Return a local-only diff of WHAT THIS TASK CHANGED, so a reviewer isn't blind.

        The caller supplies only a durable task id; base/branch/baseline refs come from runner-owned
        task metadata, so the API never accepts arbitrary refs.
          - ISOLATED task: base HEAD ...task-branch (the worktree branch).
          - NON-ISOLATED task: the task's `baseline_commit` vs the current working tree. A non-isolated
            task commits STRAIGHT INTO the workspace (no diverged branch), so a plain working-tree diff
            is empty after a checkpoint commit; diffing the recorded baseline captures the committed work
            plus any uncommitted tracked edits. (This used to raise for non-isolated tasks -> the overnight
            adversarial ladder reviewed an EMPTY diff and every critique was a false "no code" alarm.)
        The diff text is exposed under both `diff` and `stdout`.
        """
        task = self._read_task(task_id)
        worktree = task.get("worktree") if isinstance(task.get("worktree"), dict) else None
        if worktree and not task.get("worktree_finished"):
            branch = worktree.get("branch")
            expected_branch = f"task/{task_id}"
            if branch != expected_branch:
                raise PolicyError("task branch metadata is invalid")
            base = self._workspace(worktree.get("base"), write=False)
            if not base.git or not (base.root / ".git").exists():
                raise PolicyError("task base is not a Git repository")
            if self._git(base, ["cat-file", "-t", branch])["stdout"].strip() != "commit":
                raise PolicyError("task branch is not present in the base repository")
            result = self._git(base, ["diff", "--no-ext-diff", "--no-color", f"HEAD...{branch}"])
            result["diff_source"] = "task_branch"
            result["base_workspace"] = base.id
            result["branch"] = branch
        else:
            baseline = task.get("baseline_commit")
            ws = self._workspace(task.get("workspace"), write=False)
            if not ws.git or not (ws.root / ".git").exists():
                raise PolicyError("task workspace is not a Git repository")
            if not baseline or not _SHA_RE.fullmatch(str(baseline)):
                raise PolicyError("task has no baseline commit to diff against")
            if self._git(ws, ["cat-file", "-t", baseline])["stdout"].strip() != "commit":
                raise PolicyError("task baseline commit is not present")
            result = self._git(ws, ["diff", "--no-ext-diff", "--no-color", baseline])
            result["diff_source"] = "task_baseline"
            result["base_workspace"] = ws.id
            result["baseline_commit"] = baseline
        result["diff"] = result.get("stdout", "")
        result["untracked_included"] = []
        result["untracked_skipped"] = []
        self._audit("task_branch_diff", result.get("base_workspace"),
                    {"task_id": task_id, "diff_source": result["diff_source"]})
        return result

    def git_log(self, workspace_id: str, *, limit: int = 10) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        limit = max(1, min(limit, 50))
        result = self._git(
            workspace,
            ["log", f"-{limit}", "--date=iso-strict", "--pretty=format:%h%x09%ad%x09%s"],
        )
        self._audit("git_log", workspace_id, {"limit": limit})
        return result

    def _ahead_of_sync_baseline(self, workspace: Workspace) -> tuple[bool, str]:
        """Is workspace HEAD beyond the last ``sync-to-live baseline`` commit (= undeployed work)?

        Returns ``(ahead, baseline_sha)``. The D0 staleness guard uses this to distinguish a
        workspace that is legitimately AHEAD of live (this run's own end-of-item commits sit on top
        of the sync baseline) from one that is merely STALE (HEAD *is* an old baseline while live has
        moved). No baseline commit found, or any git error -> ``(True, "")`` — optimistic, so a
        never-synced or ungit-able workspace is never blocked by the guard (fail-open)."""
        if not workspace.git or not (workspace.root / ".git").exists():
            return True, ""
        try:
            found = self._git(
                workspace, ["log", "--grep", "^sync-to-live baseline", "-n", "1", "--format=%H"])
            lines = [ln for ln in (found.get("stdout") or "").splitlines() if ln.strip()]
            baseline = lines[0].strip() if lines else ""
            if not baseline:
                return True, ""
            head = self._git(workspace, ["rev-parse", "HEAD"])
            head_sha = (head.get("stdout") or "").strip()
            return (head_sha != baseline), baseline
        except (PolicyError, RunnerError, ConflictError):
            return True, ""

    def source_hashes(self, workspace_id: str) -> dict[str, Any]:
        """Fingerprint the workspace's tracked source for the D0 staleness guard: top-level ``*.py``
        (skip ``*.bak``) + ``tests/*.py``, each with its sha256, plus the ``ahead`` flag. The
        orchestrator compares this against its own ``/app`` manifest (top-level *.py only, since its
        live container has no tests dir); the extra ``tests/`` entries are provided for completeness."""
        workspace = self._workspace(workspace_id)
        root = workspace.root
        files: list[dict[str, str]] = []
        for p in sorted(root.glob("*.py")):
            if p.name.endswith(".bak") or not p.is_file():
                continue
            try:
                files.append({"rel": p.name, "sha256": _sha256(p.read_bytes())})
            except OSError:
                continue
        tdir = root / "tests"
        if tdir.is_dir():
            for p in sorted(tdir.glob("*.py")):
                if not p.is_file():
                    continue
                try:
                    files.append({"rel": f"tests/{p.name}", "sha256": _sha256(p.read_bytes())})
                except OSError:
                    continue
        ahead, baseline = self._ahead_of_sync_baseline(workspace)
        self._audit("source_hashes", workspace_id, {"files": len(files), "ahead": ahead})
        return {"files": files, "ahead": ahead, "baseline": baseline}

    def apply_patch(
        self,
        workspace_id: str,
        path: str,
        *,
        old: str,
        new: str,
        expected_sha256: str,
        replace_all: bool = False,
    ) -> dict[str, Any]:
        workspace = self._workspace(workspace_id, write=True)
        target, rel = self._resolve(workspace, path, write=True)
        if not target.is_file():
            raise PolicyError("apply_patch path must be an existing text file")
        data = target.read_bytes()
        # No size gate here by design - refusal is carried by the sha anchor (stale copy = mechanical
        # refusal), unique-match old-text, protected paths, and the sandbox suite. Binary stays
        # refused: a text patch against binary bytes is a category error, not a size policy.
        if b"\x00" in data[:8_192]:
            raise PolicyError("file is binary - not text-patchable")
        current_hash = _sha256(data)
        if not expected_sha256:
            raise ConflictError(NO_SHA)
        if current_hash != expected_sha256:
            raise ConflictError("file changed since it was read")
        text = data.decode("utf-8")
        occurrences = text.count(old)
        if occurrences == 0:
            raise ConflictError("old text was not found")
        if not replace_all and occurrences != 1:
            raise ConflictError(f"old text is ambiguous ({occurrences} matches)")
        updated = text.replace(old, new) if replace_all else text.replace(old, new, 1)
        encoded = updated.encode("utf-8")
        target.write_bytes(encoded)
        new_hash = _sha256(encoded)
        self._audit(
            "apply_patch",
            workspace_id,
            {"path": rel, "old_sha256": current_hash, "new_sha256": new_hash, "replace_all": replace_all},
        )
        return {
            "path": rel,
            "old_sha256": current_hash,
            "new_sha256": new_hash,
            "replacements": occurrences if replace_all else 1,
        }

    # A bulk replace is two calls with one rule between them: the dry run is the preview, and the
    # apply is accepted only while every file still hashes to what the preview saw and the glob
    # still matches the same set of files. Anything else refuses the whole call, so a mechanical
    # change never lands on half a workspace. Every file keeps apply_patch's confinement,
    # protected-path and binary rules; the preview is capped so the caller can actually read it.
    def _replace_targets(self, workspace: Workspace, pattern: str) -> list[tuple[str, Path]]:
        """The files a glob matches inside the workspace: existing files only, never a protected or
        ignored path, never a path that resolves outside the workspace."""
        protected = set(workspace.protected_paths)
        targets: list[tuple[str, Path]] = []
        for path in sorted(workspace.root.glob(pattern)):
            if not path.is_file():
                continue
            rel = path.relative_to(workspace.root).as_posix()
            if ".git" in Path(rel).parts:
                continue                                            # a replace never touches Git history
            if any(rel == prefix or rel.startswith(prefix.rstrip("/") + "/") for prefix in protected):
                continue
            if self._is_ignored(workspace, rel):
                continue
            try:
                resolved = path.resolve(strict=True)
                resolved.relative_to(workspace.root)
            except (OSError, ValueError) as exc:
                raise PolicyError(f"the glob matched a path outside the workspace: {rel}") from exc
            targets.append((rel, resolved))
        return targets

    @staticmethod
    def _replace_preview(text: str, updated: str) -> list[dict[str, Any]]:
        """Up to REPLACE_SAMPLE_LIMIT changed lines, each shown before and after, so the caller
        reads the effect instead of trusting a count."""
        samples: list[dict[str, Any]] = []
        for index, (before, after) in enumerate(zip(text.splitlines(), updated.splitlines()), start=1):
            if before == after:
                continue
            samples.append({
                "line": index,
                "before": before[:REPLACE_SAMPLE_CHARS],
                "after": after[:REPLACE_SAMPLE_CHARS],
            })
            if len(samples) >= REPLACE_SAMPLE_LIMIT:
                break
        return samples

    def _task_workspace(self, workspace_id: str, task_id: str | None) -> Workspace:
        """A bulk replace named by a task runs inside that task's own tree, never the base:
        an isolated task's files live in its worktree, and editing the base instead would write
        to a tree the task never sees."""
        if not task_id:
            return self._workspace(workspace_id, write=True)
        task = self._read_task(task_id)
        worktree = task.get("worktree")
        active = worktree["workspace"] if isinstance(worktree, dict) and not task.get("worktree_finished") \
            else task.get("workspace")
        if workspace_id not in (active, task.get("workspace"), (worktree or {}).get("base")):
            raise PolicyError("task %s does not belong to workspace %s" % (task_id, workspace_id))
        return self._workspace(active, write=True)

    def replace_files(
        self,
        workspace_id: str,
        *,
        glob: str,
        find: str,
        replace: str,
        regex: bool = False,
        max_files: int,
        dry_run: bool = True,
        files: list[dict[str, Any]] | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        workspace = self._task_workspace(workspace_id, task_id)
        pattern = self._relative_text(glob)
        if not find:
            raise PolicyError("find must not be empty")
        if not isinstance(max_files, int) or isinstance(max_files, bool) or max_files < 1:
            raise PolicyError("max_files is required: state how many files you expect this to touch at most")
        if max_files > REPLACE_MAX_FILES:
            raise PolicyError(f"max_files may not exceed {REPLACE_MAX_FILES}")
        matcher = None
        if regex:
            try:
                matcher = re.compile(find)
            except re.error as exc:
                raise PolicyError(f"invalid regex: {exc}") from exc

        def render(text: str) -> str:
            return matcher.sub(replace, text) if matcher else text.replace(find, replace)

        matched: list[tuple[str, Path, int]] = []
        for rel, target in self._replace_targets(workspace, pattern):
            data = target.read_bytes()
            if b"\x00" in data[:8_192]:
                continue                                            # binary: not text-replaceable
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                continue                                            # not UTF-8 text: left alone
            count = len(matcher.findall(text)) if matcher else text.count(find)
            if count:
                matched.append((rel, target, count))

        if len(matched) > max_files:
            raise PolicyError(
                "the glob matches %d files with this find, more than the stated max_files of %d; "
                "nothing was written - narrow the glob or raise the limit deliberately"
                % (len(matched), max_files)
            )

        if dry_run:
            preview = []
            for rel, target, count in matched:
                text = target.read_text(encoding="utf-8")
                preview.append({
                    "path": rel,
                    "sha256": _sha256(target.read_bytes()),
                    "matches": count,
                    "samples": self._replace_preview(text, render(text)),
                })
            self._audit("replace_dry_run", workspace.id, {
                "glob": pattern, "find": find[:200], "files": len(preview),
                "matches": sum(item["matches"] for item in preview),
            })
            return {
                "dry_run": True,
                "files": preview,
                "file_count": len(preview),
                "total_matches": sum(item["matches"] for item in preview),
                "note": "Nothing was written. Apply with this file list; it is refused if any file "
                        "changed or the glob now matches a different set.",
            }

        if not files:
            raise PolicyError("apply needs the file list the dry run returned")
        wanted: dict[str, str] = {}
        for item in files:
            path, sha = item.get("path"), item.get("sha256")
            if not path or not sha:
                raise PolicyError("every file in the list needs a path and a sha256")
            wanted[self._relative_text(path)] = sha
        live = {rel: target for rel, target, _count in matched}
        if set(wanted) != set(live):
            gone = sorted(set(wanted) - set(live))
            new = sorted(set(live) - set(wanted))
            detail = []
            if gone:
                detail.append("no longer matching: %s" % ", ".join(gone))
            if new:
                detail.append("newly matching: %s" % ", ".join(new))
            raise ConflictError("the file set changed since the dry run - nothing was written"
                                + (" (" + "; ".join(detail) + ")" if detail else ""))

        staged: list[tuple[Path, bytes, bytes, str, str, int]] = []
        for rel, target, count in matched:
            data = target.read_bytes()
            current = _sha256(data)
            if current != wanted[rel]:
                raise ConflictError(f"file changed since the dry run: {rel} - nothing was written")
            encoded = render(data.decode("utf-8")).encode("utf-8")
            staged.append((target, data, encoded, current, _sha256(encoded), count))

        written: list[tuple[Path, bytes]] = []
        result: list[dict[str, Any]] = []
        try:
            for target, before, encoded, old_sha, new_sha, count in staged:
                target.write_bytes(encoded)
                written.append((target, before))
                result.append({"path": target.relative_to(workspace.root).as_posix(),
                               "old_sha256": old_sha, "new_sha256": new_sha, "replacements": count})
        except OSError as exc:
            for target, before in written:            # half a workspace is worse than none
                try:
                    target.write_bytes(before)        # back to the bytes this call found there
                except OSError:
                    pass
            raise ConflictError(f"a file could not be written ({exc}); every written file was restored "
                                "to what it held before this call") from exc

        self._audit("replace_apply", workspace.id, {
            "glob": pattern, "find": find[:200], "files": len(result),
            "matches": sum(item["replacements"] for item in result),
        })
        return {
            "dry_run": False,
            "files": result,
            "file_count": len(result),
            "total_replacements": sum(item["replacements"] for item in result),
            "workspace": workspace.id,
            "git_status": self.git_status(workspace.id),
        }

    def file_create(
        self,
        workspace_id: str,
        path: str,
        *,
        content: str,
    ) -> dict[str, Any]:
        workspace = self._workspace(workspace_id, write=True)
        target, rel = self._resolve(workspace, path, write=True, allow_missing=True)
        if target.exists():
            raise ConflictError("file already exists")
        data = content.encode("utf-8")
        # Parent dirs are created here; _resolve has already confined the path to the workspace,
        # so the parents are inside it by construction.
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        digest = _sha256(data)
        self._audit("file_create", workspace_id, {"path": rel, "sha256": digest})
        return {"path": rel, "sha256": digest}

    # The right-tools set. Three doors, each confined to the workspace by _resolve:
    # file_create_bytes a fetched file lands as a FILE (the orchestrator fetched it through its guarded web door)
    # file_read_bytes a picture goes back to the orchestrator, for a model that can look at it
    # render the file as a standard viewer shows it (render.py)
    def file_create_bytes(self, workspace_id: str, path: str, *, data: bytes) -> dict[str, Any]:
        workspace = self._workspace(workspace_id, write=True)
        target, rel = self._resolve(workspace, path, write=True, allow_missing=True)
        if target.exists():
            raise ConflictError("file already exists")
        if len(data) > BYTES_MAX:
            raise PolicyError(f"{len(data)} bytes is over this runner's {BYTES_MAX}-byte file limit")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        digest = _sha256(data)
        self._audit("file_create_bytes", workspace_id, {"path": rel, "sha256": digest, "bytes": len(data)})
        return {"path": rel, "sha256": digest, "bytes": len(data)}

    def file_read_bytes(self, workspace_id: str, path: str) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        target, rel = self._resolve(workspace, path)
        if not target.is_file():
            raise PolicyError("file_read_bytes path must be a file")
        size = target.stat().st_size
        if size > BYTES_MAX:
            raise PolicyError(f"{rel} is {size} bytes, over this runner's {BYTES_MAX}-byte file limit")
        data = target.read_bytes()
        self._audit("file_read_bytes", workspace_id, {"path": rel, "bytes": len(data)})
        return {"path": rel, "sha256": _sha256(data), "bytes": len(data), "data": data}

    def page_bundle(self, workspace_id: str, path: str) -> dict[str, Any]:
        """The page's own folder as a gzipped tar, for a render on a GPU factory. Git's records and earlier renders stay
        behind. A folder whose files name `../` needs what they reach, so the bundle starts that many levels up, never
        above the workspace. If the wider bundle is too big it narrows, level by level, and over BYTES_MAX at the page's own
        folder is refused: the caller then draws here. `page` is the page's path inside the bundle."""
        import io
        import tarfile
        workspace = self._workspace(workspace_id)
        target, rel = self._resolve(workspace, path)
        if not target.is_file():
            raise PolicyError("render shows a file; %s is not one" % rel)
        left_behind = (".git", "renders", "__pycache__")

        def bundle(base):
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w:gz") as tar:
                for f in sorted(base.rglob("*")):
                    parts = f.relative_to(base).parts
                    if f.is_file() and not f.is_symlink() and not any(p in left_behind for p in parts):
                        tar.add(f, arcname="/".join(parts), recursive=False)
            return buf.getvalue()

        folder = target.parent
        up = 0
        for f in folder.rglob("*"):
            if (f.is_file() and not f.is_symlink() and f.suffix.lower() in PAGE_TEXT_TYPES and f.stat().st_size <= PAGE_TEXT_MAX
                    and not any(p in left_behind for p in f.relative_to(folder).parts)):
                for m in re.finditer(r"(?:\.\./)+", f.read_text(encoding="utf-8", errors="replace")):
                    up = max(up, m.group(0).count("../"))
        bases = [folder]
        root = workspace.root.resolve()
        for parent in folder.parents:
            if len(bases) > up or parent != root and root not in parent.parents:
                break
            bases.append(parent)
        for base in reversed(bases):
            data = bundle(base)
            if len(data) <= BYTES_MAX:
                break
        if len(data) > BYTES_MAX:
            raise PolicyError(f"{rel}'s folder bundles to {len(data)} bytes, over this runner's {BYTES_MAX}-byte file limit")
        self._audit("page_bundle", workspace_id, {"path": rel, "bytes": len(data)})
        return {"page": target.relative_to(base).as_posix(), "bundle": data, "bytes": len(data)}

    def file_shots(self, workspace_id: str, path: str, *, result: dict, pictures: dict) -> dict[str, Any]:
        """Pictures a GPU factory drew, filed where render() files its own: renders/ beside the page, a re-render replacing."""
        workspace = self._workspace(workspace_id, write=True)
        target, rel = self._resolve(workspace, path)
        out_dir, out_rel = self._resolve(workspace, str(Path(rel).parent / "renders"), write=True, allow_missing=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        out = dict(result)
        shots = []
        for shot in out.get("shots") or []:
            name = Path(shot["file"]).name
            (out_dir / name).write_bytes(pictures[shot["file"]])
            shots.append({k: v for k, v in dict(shot, path=f"{out_rel}/{name}").items() if k != "file"})
        out["shots"] = shots
        self._audit("file_shots", workspace_id, {"path": rel, "shots": len(shots)})
        return dict(out, path=rel, rendered=True)

    def render(self, workspace_id: str, path: str, *, width: int = 400, height: int = 400, steps=None,
               wait_ms: int = 1500, views=None, url: str | None = None, query: str = "") -> dict[str, Any]:
        """`url`: a PUBLIC web page opened in the browser instead of a file; `path` is then the workspace FOLDER its
        pictures go under. The page's address passes the same rule as every request it makes (netguard)."""
        import render as _render
        workspace = self._workspace(workspace_id, write=True)
        if url:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            why = ("only http and https pages open" if parsed.scheme not in ("http", "https")
                   else _render.netguard.public_refusal(parsed.hostname or ""))
            if why:
                raise PolicyError(f"that address is refused: {why}")
            out_rel = (Path(self._relative_text(path)) / "renders").as_posix()   # `path`: the job's folder
            out_dir, out_rel = self._resolve(workspace, out_rel, write=True, allow_missing=True)
            try:
                result = _render.render_page(url, workspace.root, out_dir, width=width, height=height,
                                             steps=steps, wait_ms=wait_ms)
            except _render.RenderError as exc:
                self._audit("render", workspace_id, {"url": url[:200], "refused": str(exc)[:200]})
                return {"url": url, "rendered": False, "why": str(exc)}
            for shot in result["shots"]:
                shot["path"] = f"{out_rel}/{shot.pop('file')}"
            self._audit("render", workspace_id, {"url": url[:200], "shots": len(result["shots"])})
            return dict(result, rendered=True)
        target, rel = self._resolve(workspace, path)
        if not target.is_file():
            raise PolicyError("render path must be a file")
        # the pictures land beside the file, in renders/: derived output, replaced by the next render of that file
        out_rel = (Path(rel).parent / "renders").as_posix()
        out_dir, out_rel = self._resolve(workspace, out_rel, write=True, allow_missing=True)
        kind = target.suffix.lower()
        try:
            if kind in _render.PAGE_TYPES:
                result = _render.render_page(target, workspace.root, out_dir, width=width, height=height,
                                             steps=steps, wait_ms=wait_ms, query=query)
            elif kind in _render.STL_TYPES:
                result = _render.render_stl(target, out_dir, views=views)
            else:
                raise PolicyError("render shows %s files; %s is neither (a .png or .jpg needs no render: look at it)" % (
                    ", ".join(_render.PAGE_TYPES + _render.STL_TYPES), kind or "this file"))
        except _render.RenderError as exc:
            # a file a viewer cannot open is the FINDING, said in the viewer's words - not a runner failure
            self._audit("render", workspace_id, {"path": rel, "refused": str(exc)[:200]})
            return {"path": rel, "rendered": False, "why": str(exc)}
        for shot in result["shots"]:
            shot["path"] = f"{out_rel}/{shot.pop('file')}"
        self._audit("render", workspace_id, {"path": rel, "kind": result["kind"], "shots": len(result["shots"])})
        return dict(result, path=rel, rendered=True)

    def file_move(
        self,
        workspace_id: str,
        source: str,
        destination: str,
        *,
        expected_sha256: str,
    ) -> dict[str, Any]:
        workspace = self._workspace(workspace_id, write=True)
        source_path, source_rel = self._resolve(workspace, source, write=True)
        destination_path, destination_rel = self._resolve(
            workspace,
            destination,
            write=True,
            allow_missing=True,
        )
        if not source_path.is_file():
            raise PolicyError("file_move source must be an existing file")
        if destination_path.exists():
            raise ConflictError("file_move destination already exists")
        data = source_path.read_bytes()
        current_hash = _sha256(data)
        if not expected_sha256:
            raise ConflictError(NO_SHA)
        if current_hash != expected_sha256:
            raise ConflictError("source file changed since it was read")
        source_path.replace(destination_path)
        self._audit(
            "file_move",
            workspace_id,
            {"source": source_rel, "destination": destination_rel, "sha256": current_hash},
        )
        return {"source": source_rel, "destination": destination_rel, "sha256": current_hash}

    def job_ship(self, workspace_id: str, path: str, *, owner: str, chat_id: str,
                 ship_root: str | None = None) -> dict[str, Any]:
        """Ship a job's folder when its run ends. A job that ended INSIDE an unfinished task of its own (its budget ran
        out mid-task, a merge was refused) hands over that task's folder as its current result, which is what its prompt
        promised ("at the deadline ... hand over your current result"). The shipment is labelled
        unfinished in its name and receipt, the base's own copy leaves as a second shipment, and the task is abandoned by
        the runner's own rule (cancelled, its work kept in a checkpoint, its worktree removed), so the next blind job
        finds nothing, and the case never holds the next job. Still refused: two of the job's tasks holding the folder,
        and a shipment whose task could not be ended.
        Shipping is transport and isolation, never a statement about the artifact's quality."""
        if not owner or not chat_id:
            raise PolicyError("job shipping requires its owner and chat id")
        base = self._workspace(workspace_id, write=True)
        self._resolve(base, path, write=True, allow_missing=True)
        sources = []
        for record in self.tasks_dir.glob("*.json"):
            task = json.loads(record.read_text(encoding="utf-8"))
            wt = task.get("worktree") or {}
            if (task.get("owner") != owner or task.get("chat_id") != chat_id
                    or wt.get("base") != workspace_id or task.get("worktree_finished")):
                continue
            ws_id = wt.get("workspace")
            if ws_id not in self.workspaces and Path(wt["path"]).is_dir():
                self._register_worktree_workspace(base, ws_id, Path(wt["path"]).resolve(), wt["branch"], task["id"])
            ws = self._workspace(ws_id, write=True)
            target, _ = self._resolve(ws, path, write=True, allow_missing=True)
            if target.exists():
                sources.append((task["id"], ws_id))
        if len(sources) > 1:
            raise ConflictError("multiple owned task worktrees hold this job folder; nothing was shipped")
        if not sources:
            return self.file_ship(workspace_id, path, ship_root=ship_root)
        import shutil
        task_id, ws_id = sources[0]
        root = Path(ship_root or os.environ.get("CODING_SHIP_ROOT", "/app/shipped"))
        if not root.is_dir():
            raise PolicyError("no shipping area is mounted at %s; nothing was shipped" % root)
        ws = self._workspace(ws_id, write=True)
        target, rel = self._resolve(ws, path, write=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        dest = root / workspace_id / ("%s-%s__unfinished-task-%s" % (stamp, rel.replace("/", "__"), task_id[:8]))
        if dest.exists():
            raise ConflictError("the shipping destination already exists: %s" % dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if target.is_dir():
            shutil.copytree(target, dest)
            want = sum(1 for p in target.rglob("*") if p.is_file())
            got = sum(1 for p in dest.rglob("*") if p.is_file())
        else:
            shutil.copy2(target, dest)
            want, got = 1, 1 if dest.is_file() else 0
        if want != got:
            shutil.rmtree(dest, ignore_errors=True) if dest.is_dir() else dest.unlink(missing_ok=True)
            raise ConflictError("the shipped copy is short (%d of %d files); nothing was taken out and the task is untouched"
                                % (got, want))
        base_target, _ = self._resolve(base, path, write=True, allow_missing=True)
        base_out = self.file_ship(workspace_id, path, ship_root=ship_root) if base_target.exists() else None
        ended = self.task_abandon(task_id)
        if not self._read_task(task_id).get("worktree_finished"):
            raise ConflictError("shipped to %s, but task %s's worktree could not be removed, so the next job could still "
                                "read it; the shipment stands and the task is as it was" % (dest, task_id))
        self._audit("job_ship_unfinished", workspace_id, {"path": rel, "shipped_to": str(dest), "files": want,
                                                          "task_id": task_id})
        return {"path": rel, "shipped_to": str(dest), "files": want, "tracked": False, "unfinished_task": True,
                "source_task_id": task_id, "logical_workspace": workspace_id, "source_workspace": ws_id,
                "preserved_checkpoint": ended.get("preserved_checkpoint"),
                "supporting_base_shipment": (base_out or {}).get("shipped_to")}

    def file_ship(self, workspace_id: str, path: str, *, ship_root: str | None = None) -> dict[str, Any]:
        """Ship a finished job's folder (or file) OUT of the workspace, so the next job in it never sees it: factory orders
        ship outside, to a different area. It is copied to the shipping area
        (CODING_SHIP_ROOT, mounted beside the workspace; never created here, so an unmounted area refuses instead of filling
        the container), the copy is counted, and only then is it taken out: `git rm` + a commit when git tracks it, removed
        otherwise. A short copy removes nothing. -> {path, shipped_to, files, tracked}."""
        import shutil
        workspace = self._workspace(workspace_id, write=True)
        target, rel = self._resolve(workspace, path, write=True)
        if target == workspace.root:
            raise PolicyError("file_ship ships a job's folder, never the whole workspace")
        root = Path(ship_root or os.environ.get("CODING_SHIP_ROOT", "/app/shipped"))
        if not root.is_dir():
            raise PolicyError("no shipping area is mounted at %s; nothing was shipped" % root)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        dest = root / workspace_id / ("%s-%s" % (stamp, rel.replace("/", "__")))
        if dest.exists():
            raise ConflictError("the shipping destination already exists: %s" % dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if target.is_dir():
            shutil.copytree(target, dest)
            want = sum(1 for p in target.rglob("*") if p.is_file())
            got = sum(1 for p in dest.rglob("*") if p.is_file())
        else:
            shutil.copy2(target, dest)
            want, got = 1, 1 if dest.is_file() else 0
        if want != got:
            shutil.rmtree(dest, ignore_errors=True) if dest.is_dir() else dest.unlink(missing_ok=True)
            raise ConflictError("the shipped copy is short (%d of %d files); nothing was taken out of the workspace" % (got, want))
        tracked = False
        if workspace.git and (workspace.root / ".git").exists():
            listed = self._git(workspace, ["ls-files", "--", rel])
            tracked = bool(listed.get("returncode") == 0 and (listed.get("stdout") or "").strip())
        if tracked:
            removed = self._git(workspace, ["rm", "-r", "-q", "--", rel])
            if removed["returncode"] != 0:
                raise ConflictError("shipped to %s, but git could not take it out (%s)" % (dest, removed["stderr"].strip()[:200]))
            done = self._git(workspace, [*GIT_IDENTITY, "commit", "-q", "-m",
                                         "Ship %s out of the workspace (%d files, to %s)" % (rel, want, dest)])
            if done["returncode"] != 0:
                raise ConflictError("shipped and removed, but the commit failed (%s)" % ((done["stderr"] or done["stdout"]).strip()[:200],))
        if target.exists():          # untracked, or what git rm left (ignored files): out of the workspace too
            shutil.rmtree(target) if target.is_dir() else target.unlink()
        self._audit("file_ship", workspace_id, {"path": rel, "shipped_to": str(dest), "files": want, "tracked": tracked})
        return {"path": rel, "shipped_to": str(dest), "files": want, "tracked": tracked}

    def file_delete(
        self,
        workspace_id: str,
        path: str,
        *,
        expected_sha256: str,
    ) -> dict[str, Any]:
        workspace = self._workspace(workspace_id, write=True)
        target, rel = self._resolve(workspace, path, write=True)
        if not target.is_file():
            raise PolicyError("file_delete path must be an existing file")
        data = target.read_bytes()
        current_hash = _sha256(data)
        if not expected_sha256:
            raise ConflictError(NO_SHA)
        if current_hash != expected_sha256:
            raise ConflictError("file changed since it was read")
        target.unlink()
        self._audit("file_delete", workspace_id, {"path": rel, "sha256": current_hash})
        return {"path": rel, "deleted": True, "sha256": current_hash}

    def test_run(self, workspace_id: str, profile: str, path: str | None = None, worker: str | None = None) -> dict[str, Any]:
        """Run an allow-listed profile. `path` (optional) runs it from that folder of the workspace instead of its root,
        so a shared scratch workspace ("one folder per job") collects only that job's tests, never another job's (a
        finished job's browser render tests can outlast the caller's wait)."""
        workspace = self._workspace(workspace_id)
        argv = workspace.commands.get(profile)
        if argv is None:
            raise PolicyError(f"unknown test profile: {profile}")
        cwd, rel = workspace.root, None
        if path:
            cwd, rel = self._resolve(workspace, path)
            if not cwd.is_dir():
                raise PolicyError(f"test path is not a folder: {rel}")
        url = self.test_workers.get(worker) if worker and workspace.shared else None
        result, why_here = (self._test_on_worker(workspace, list(argv), cwd, url, worker) if url else (None, None))
        if result is None:
            result = self._run(list(argv), cwd, timeout=self.test_run_timeout_seconds, idle=self.test_idle_seconds, memory_gb=self.test_memory_gb)
            if why_here:
                result["ran_here_because"] = why_here
        result["timeout_seconds"] = self.test_run_timeout_seconds
        if rel:
            result["path"] = rel
        self._audit(
            "test_run",
            workspace_id,
            {"profile": profile, "path": rel, "returncode": result["returncode"], "timed_out": result["timed_out"]},
        )
        return result

    def _test_on_worker(self, workspace: Workspace, argv: list[str], cwd: Path, url: str, host: str) -> tuple[dict[str, Any] | None, str | None]:
        """One test run on the worker box -> (the result, None), or (None, why it should run here). Never raises: a box that cannot take it
        costs the job nothing, the run happens here as it always did, and the result says so. What the run wrote comes back into the job's folder."""
        import base64
        import io
        import tarfile
        import urllib.error
        import urllib.request

        def call(path, body=None, timeout=None):
            req = urllib.request.Request(url + path, data=json.dumps(body).encode("utf-8") if body is not None else None,
                                         headers={"Content-Type": "application/json", "x-coding-token": self.test_worker_token})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))

        try:
            call("/health", timeout=self.test_worker_answer_seconds)   # a dead box answers here in seconds, not after the run's whole allowance
        except Exception as exc:                                     # noqa: BLE001
            return None, "the test box did not answer (%s)" % type(exc).__name__
        root = workspace.root.resolve()
        left_behind = {".git", "__pycache__", ".pytest_cache", "renders"}
        protected = {str(p).strip("/") for p in (workspace.protected_paths or ())}

        def sent(rel_parts):
            return not any(p in left_behind for p in rel_parts) and "/".join(rel_parts) not in protected and rel_parts[0] not in protected

        buf = io.BytesIO()
        try:
            with tarfile.open(fileobj=buf, mode="w:gz") as tar:
                for f in sorted(root.rglob("*")):
                    parts = f.relative_to(root).parts
                    if f.is_file() and not f.is_symlink() and sent(parts):
                        tar.add(f, arcname="/".join(parts), recursive=False)
        except OSError as exc:
            return None, "its folder could not be bundled (%s)" % type(exc).__name__
        data = buf.getvalue()
        if len(data) > BYTES_MAX:
            return None, "its folder bundles to %d bytes, over the %d-byte limit" % (len(data), BYTES_MAX)
        body = {"argv": argv, "bundle_b64": base64.b64encode(data).decode("ascii"), "cwd_rel": cwd.resolve().relative_to(root).as_posix(),
                "timeout": self.test_run_timeout_seconds, "idle": self.test_idle_seconds, "memory_gb": self.test_memory_gb}
        try:
            result = call("/run", body, timeout=self.test_run_timeout_seconds + 300)
        except urllib.error.HTTPError as exc:                        # the box decides whether it has room (test_worker.admit), and says why not
            try:
                said = json.loads(exc.read().decode("utf-8", "replace")).get("error")
            except (OSError, ValueError):
                said = None
            return None, said or "the test box refused the run (HTTP %d)" % exc.code
        except Exception as exc:                                     # noqa: BLE001 - a lost box is not the job's failure
            return None, "the test box stopped answering during the run (%s)" % type(exc).__name__
        changed = result.pop("changed_b64", None)
        deleted = result.pop("deleted", None) or []
        try:
            if changed:
                with tarfile.open(fileobj=io.BytesIO(base64.b64decode(changed)), mode="r:gz") as tar:
                    for m in tar.getmembers():
                        target = (root / m.name).resolve()
                        if not m.isfile() or (root not in target.parents) or not sent(Path(m.name).parts):
                            continue
                        target.parent.mkdir(parents=True, exist_ok=True)
                        src = tar.extractfile(m)
                        if src is not None:
                            target.write_bytes(src.read())
            for name in deleted:
                target = (root / name).resolve()
                if root in target.parents and sent(Path(name).parts) and target.is_file():
                    target.unlink()
        except (OSError, tarfile.TarError) as exc:
            result["changes_not_carried"] = "writing them back failed (%s)" % type(exc).__name__
        result["ran_on"] = host
        return result, None

    @staticmethod
    def _broad_test_failure(result: dict[str, Any]) -> bool:
        output = f"{result.get('stdout', '')}\n{result.get('stderr', '')}"
        return (any(marker in output for marker in ("ImportError", "ModuleNotFoundError", "_FailedTest"))
                or sum(1 for line in output.splitlines() if line.startswith("ERROR:")) >= 5)

    def _restore_workspace_commit(self, workspace_id: str, commit: str) -> dict[str, Any]:
        workspace = self._workspace(workspace_id, write=True)
        if not commit or not _SHA_RE.fullmatch(str(commit)):
            raise PolicyError("no restorable baseline commit")
        kind = self._git(workspace, ["cat-file", "-t", commit])
        if kind["stdout"].strip() != "commit":
            raise PolicyError("baseline commit is not present in this workspace")
        self._git(workspace, ["reset", "--hard", commit])
        clean_args = ["clean", "-fd"]
        for prefix in workspace.protected_paths:
            clean_args += ["-e", prefix]
        self._git(workspace, clean_args)
        return {"restored_commit": commit,
                "head": self._git(workspace, ["rev-parse", "HEAD"])["stdout"].strip(),
                "git_status": self.git_status(workspace_id)}

    def test_run_recover(self, workspace_id: str, profile: str, task_id: str, path: str | None = None, worker: str | None = None) -> dict[str, Any]:
        task = self.task_get(task_id)
        if task["workspace"] != workspace_id:
            raise PolicyError("task does not belong to this workspace")
        recovery = task.get("recovery") or {}
        if recovery.get("action") == "blocked_repeated_failure":
            return {"returncode": recovery.get("returncode", 1), "stdout": "", "stderr": "",
                    "timed_out": False, "retry_blocked": True, "recovery": recovery}
        result = self.test_run(workspace_id, profile, path, worker)
        if result["returncode"] == 0:
            task.pop("recovery", None)
            self._write_task(task)
            return result
        fingerprint = _sha256((profile + "\0" + result.get("stdout", "") + "\0"
                               + result.get("stderr", "")).encode("utf-8"))
        count = recovery.get("count", 0) + 1 if recovery.get("fingerprint") == fingerprint else 1
        restore_count = recovery.get("restore_count", 0)
        recovery = {"fingerprint": fingerprint, "count": count, "restore_count": restore_count,
                    "returncode": result["returncode"], "action": "diagnose_and_retry"}
        if count >= 2:
            if (self._broad_test_failure(result) and task.get("baseline_commit")
                    and restore_count < 1):
                kept = self._preserve_before_reset(
                    task, why="the automatic baseline restore after a repeated broad test failure")
                restored = self._restore_workspace_commit(workspace_id, task["baseline_commit"])
                recovery.update({"action": "restored_baseline", "restore_count": 1, **restored,
                                 "preserved_checkpoint": kept})
                task.setdefault("notes", []).append(
                    {"timestamp": _utc_now(), "text": "Repeated broad test failure; restored task baseline."})
            else:
                recovery["action"] = "blocked_repeated_failure"
                task["status"] = "blocked"
                task.setdefault("notes", []).append(
                    {"timestamp": _utc_now(), "text": "Repeated identical test failure; stopped automatic retries."})
        task["recovery"] = recovery
        task["updated_at"] = _utc_now()
        self._write_task(task)
        result["recovery"] = recovery
        return result

    def _active_task_for_workspace(self, workspace_id: str) -> dict[str, Any] | None:
        """A non-isolated, non-finished active task on this workspace (single-active discipline, mirroring
        projects). Isolated (worktree) tasks are exempt - they have their own tree."""
        for path in self.tasks_dir.glob("*.json"):
            try:
                task = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if (task.get("workspace") == workspace_id and task.get("worktree") is None
                    and task.get("status") == "active"):
                return task
        return None

    def task_abandon(self, task_id: str) -> dict[str, Any]:
        """Abandon a task: cancel it AND restore its workspace to a clean baseline, so a stuck or dirty task
        cannot block or corrupt later work. Isolated tasks discard their worktree; non-isolated tasks reset
        --hard + clean to the task's baseline_commit. Idempotent on already-terminal tasks."""
        task = self._read_task(task_id)
        if task.get("status") in {"complete", "cancelled"}:
            return {"task_id": task_id, "status": task["status"], "already_terminal": True, "restored": None}
        restored = None
        shared_preserved = None
        if task.get("worktree") and not task.get("worktree_finished"):
            try:
                self.task_finish(task_id, action="discard")
            except (PolicyError, ConflictError, RunnerError) as exc:
                self._audit("task_abandon_worktree_error", task.get("workspace"),
                            {"task_id": task_id, "error": str(exc)[:200]})
        elif task.get("baseline_commit"):
            shared_preserved = self._preserve_before_reset(task)
            try:
                restored = self._restore_workspace_commit(task["workspace"], task["baseline_commit"])
            except (PolicyError, RunnerError) as exc:
                self._audit("task_abandon_restore_error", task.get("workspace"),
                            {"task_id": task_id, "error": str(exc)[:200]})
        task = self._read_task(task_id)
        preserved = (task.get("worktree_finished") or {}).get("preserved_checkpoint")
        if task.get("baseline_commit") and not task.get("worktree"):
            preserved = shared_preserved
        task["status"] = "cancelled"
        task["updated_at"] = _utc_now()
        note = "abandoned: cancelled + workspace restored to baseline"
        if preserved:
            note += f"; work preserved in checkpoint {preserved}"
        task.setdefault("notes", []).append({"timestamp": _utc_now(), "text": note})
        self._cascade_escalation_on_task_end(task, task_id, "task abandoned")
        self._write_task(task)
        self._audit("task_abandon", task.get("workspace"),
                    {"task_id": task_id, "restored": bool(restored), "preserved_checkpoint": preserved})
        return {"task_id": task_id, "status": "cancelled", "restored": restored,
                "preserved_checkpoint": preserved}
    def task_start(
        self,
        workspace_id: str,
        objective: str,
        *,
        acceptance_criteria: list[str] | None = None,
        isolate: bool = False,
        owner: str | None = None,
        chat_id: str | None = None,
        _internal: bool = False,
    ) -> dict[str, Any]:
        base = self._workspace(workspace_id, write=isolate)
        isolate = isolate or (base.shared and base.write)
        if not objective.strip():
            raise PolicyError("objective is required")
        if not isolate and not _internal:
            existing = self._active_task_for_workspace(workspace_id)
            if existing:
                raise ConflictError(
                    f"active task already exists for {workspace_id}: {existing['id']}. "
                    "Reuse it, complete it (status=complete), or abandon it first.")
            if base.git and (base.root / ".git").exists():
                dirty = self._git(base, ["status", "--short"])
                if dirty["returncode"] == 0 and dirty["stdout"].strip():
                    raise PolicyError(
                        f"workspace {workspace_id} has uncommitted changes - commit/checkpoint or abandon "
                        f"the prior task before starting a new task:\n{dirty['stdout'][:500]}")
        task_id = uuid.uuid4().hex
        worktree = self._create_worktree(base, task_id) if isolate else None
        # When isolated, the task operates inside its worktree (so file ops, checkpoints,
        # and restores act on the isolated branch, not the canonical tree).
        active_workspace = worktree["workspace"] if worktree else workspace_id
        baseline_commit = None
        active = self.workspaces.get(active_workspace)
        if active and active.git and (active.root / ".git").exists():
            head = self._git(active, ["rev-parse", "HEAD"])
            baseline_commit = head["stdout"].strip() if head["returncode"] == 0 else None
        task = {
            "id": task_id,
            "workspace": active_workspace,
            "base_workspace": workspace_id,
            "worktree": worktree,
            "baseline_commit": baseline_commit,
            "owner": owner,
            "chat_id": chat_id,
            "objective": objective.strip(),
            "acceptance_criteria": acceptance_criteria or [],
            "status": "active",
            "plan": [],
            "notes": [],
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
        }
        self._write_task(task)
        self._audit("task_start", active_workspace, {"task_id": task_id, "isolated": bool(worktree)})
        return task

    def _create_worktree(self, base: Workspace, task_id: str) -> dict[str, Any]:
        """Create a Git worktree on a fresh branch from base HEAD, registered as an
        ephemeral writable workspace. In-memory registration only (a runner restart drops
        the registration; the on-disk worktree + branch persist and can be finished manually)."""
        if not base.git or not (base.root / ".git").exists():
            raise PolicyError("worktree isolation requires a Git workspace")
        wt_id = f"wt-{task_id}"
        branch = f"task/{task_id}"
        wt_root = base.root.parent / ".worktrees" / task_id
        wt_root.parent.mkdir(parents=True, exist_ok=True)
        result = self._git(base, ["worktree", "add", "-b", branch, str(wt_root), "HEAD"])
        if result["returncode"] != 0:
            raise RunnerError(f"failed to create worktree: {result['stderr'][:300]}")
        self._register_worktree_workspace(base, wt_id, wt_root.resolve(), branch, task_id)
        self._audit("worktree_add", base.id, {"task_id": task_id, "worktree": wt_id, "branch": branch})
        return {"workspace": wt_id, "path": str(wt_root), "branch": branch, "base": base.id}

    def _register_worktree_workspace(
        self, base: Workspace, wt_id: str, root: Path, branch: str, task_id: str
    ) -> None:
        self.workspaces[wt_id] = Workspace(
            id=wt_id,
            root=root,
            description=f"Isolated worktree for task {task_id} (branch {branch} of {base.id})",
            read=True,
            write=True,
            git=True,
            protected_paths=base.protected_paths,
            ignored_paths=base.ignored_paths,
            commands=base.commands,
            shared=base.shared,
        )

    def _preserve_before_reset(self, task: dict[str, Any], target: str | None = None,
                               why: str = "abandon") -> str | None:
        """The same work-preservation for every hard reset of a NON-isolated task's workspace (`_preserve_before_discard`
        covered only the isolated path): abandon, the automatic baseline restore after a repeated broad test failure,
        and a restore to a checkpoint. `target` is the commit being reset to (default: the task's baseline). Work already
        saved by one of this task's checkpoints at the current commit, with a clean tree, is not saved again. NEVER
        raises."""
        try:
            ws_id = task.get("workspace")
            if not ws_id or ws_id not in self.workspaces:
                return None
            ws = self._workspace(ws_id)
            status = self._git(ws, ["status", "--porcelain"])
            head = self._git(ws, ["rev-parse", "HEAD"])
            head_sha = head["stdout"].strip() if head["returncode"] == 0 else None
            baseline = target or task.get("baseline_commit")
            dirty = bool(status["stdout"].strip())
            # work is lost only if the tree is dirty or HEAD has commits the reset target does not contain (a reset
            # FORWARD to a checkpoint ahead of HEAD loses nothing)
            behind = (baseline and head_sha and head_sha != baseline
                      and self._git(ws, ["merge-base", "--is-ancestor", head_sha, baseline])["returncode"] != 0)
            if not dirty and not behind:
                return None
            if not dirty and head_sha:
                for p in (self.checkpoints_dir / task["id"]).glob("*.json"):
                    try:
                        if json.loads(p.read_text(encoding="utf-8")).get("git_commit") == head_sha:
                            return None
                    except (OSError, ValueError):
                        continue
            cp = self.task_checkpoint(
                task["id"], test_profile=None,
                note="auto-preserved before %s (work-preservation safety net)" % why)
            self._audit("task_preserve_before_reset", ws_id, {"task_id": task.get("id"), "checkpoint_id": cp.get("id")})
            return cp.get("id")
        except Exception as exc:
            self._audit("task_preserve_error", task.get("workspace"),
                        {"task_id": task.get("id"), "error": str(exc)[:200]})
            return None

    def _preserve_before_discard(self, task: dict[str, Any], worktree: dict[str, Any]) -> str | None:
        """Best-effort work-preservation before a discarded task's worktree + branch are destroyed.

        A non-converged run's leaf is abandoned (task_abandon -> task_finish discard) and the
        `git branch -D` below is irreversible; without this a near-complete run leaves nothing to
        salvage. A checkpoint's diff is written to coding-state, OUTSIDE the branch, so it survives
        the deletion and a reviewer can port the work. Skips a worktree with no work so a plain
        discard does not litter empty checkpoints. NEVER raises: preservation must not break the
        discard path, or a failure here would strand a stuck task on the workspace."""
        try:
            wt_ws = worktree.get("workspace")
            if not wt_ws or wt_ws not in self.workspaces:
                return None
            ws = self._workspace(wt_ws)
            status = self._git(ws, ["status", "--porcelain"])
            head = self._git(ws, ["rev-parse", "HEAD"])
            head_sha = head["stdout"].strip() if head["returncode"] == 0 else None
            baseline = task.get("baseline_commit")
            has_work = bool(status["stdout"].strip()) or bool(baseline and head_sha and head_sha != baseline)
            if not has_work:
                return None
            cp = self.task_checkpoint(
                task["id"], test_profile=None,
                note="auto-preserved before discard (work-preservation safety net)")
            self._audit("task_preserve_before_discard", task.get("workspace"),
                        {"task_id": task.get("id"), "checkpoint_id": cp.get("id")})
            return cp.get("id")
        except Exception as exc:
            self._audit("task_preserve_error", task.get("workspace"),
                        {"task_id": task.get("id"), "error": str(exc)[:200]})
            return None

    def _uncommitted(self, workspace: Workspace) -> list[str]:
        """The worktree's changed and untracked paths (git's own short status), [] when clean. A status git could not
        give is an error, never "clean": a caller about to remove the worktree must not read silence as nothing to lose."""
        out = self._git(workspace, ["status", "--porcelain"])
        if out.get("returncode") != 0:
            raise ConflictError("git could not report the state of %s (%s)" % (
                workspace.id, ((out.get("stderr") or "").strip().splitlines() or ["no message"])[0][:200]))
        return [line for line in (out.get("stdout") or "").splitlines() if line.strip()]

    _CARRY_SKIP_PARTS = ("__pycache__", ".pytest_cache")

    def _carry_ignored(self, worktree: Workspace, base: Workspace) -> list[str]:
        """Copy the files git IGNORES in a finished worktree into the base, at the same paths, before the worktree is
        removed. A merge carries commits and a checkpoint commits only what git sees, so a job writing under an ignored
        folder lost everything when the worktree went. Copied, not committed: what git was told to keep out of
        history stays out of it, and the job's folder in the base holds its work as the job left it. Caches and the
        runner's own hidden paths are not work. A copy that fails raises, and the caller removes nothing."""
        out = self._git(worktree, ["ls-files", "--others", "--ignored", "--exclude-standard", "-z"], timeout=60)
        if out.get("returncode") != 0:
            raise ConflictError("git could not list the ignored files in %s (%s)" % (
                worktree.id, ((out.get("stderr") or "").strip().splitlines() or ["no message"])[0][:200]))
        import shutil
        carried = []
        for rel in (out.get("stdout") or "").split("\0"):
            rel = rel.strip()
            if not rel or rel.endswith(".pyc") or self._is_ignored(worktree, rel) \
                    or any(p in self._CARRY_SKIP_PARTS for p in Path(rel).parts):
                continue
            src, dest = worktree.root / rel, base.root / rel
            if not src.is_file():
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            carried.append(rel)
        return carried

    def _merge_into_base(self, base: Workspace, branch: str) -> None:
        """Fast-forward when nothing landed since the job started. On a shared workspace another job usually has, and the two sit in different folders, so they combine;
        two jobs that changed one file refuse, naming it, with nothing merged and nothing removed. Anywhere else a diverged base refuses, as it always has."""
        merge = self._git(base, ["merge", "--ff-only", branch])
        if merge["returncode"] == 0:
            return
        if not base.shared:
            raise ConflictError(f"cannot fast-forward {base.id} to {branch} (diverged); resolve manually or discard: {merge['stderr'][:200]}")
        combined = self._git(base, ["merge", "--no-edit", branch], timeout=120)
        if combined["returncode"] == 0:
            return
        clash = self._git(base, ["diff", "--name-only", "--diff-filter=U"])
        files = [x for x in (clash.get("stdout") or "").splitlines() if x.strip()]
        self._git(base, ["merge", "--abort"])
        why = ("another job changed the same file%s: %s" % ("s" if len(files) > 1 else "", ", ".join(files[:10]))) if files else (combined.get("stderr") or combined.get("stdout") or "")[:200]
        raise ConflictError(f"cannot combine {branch} with {base.id}, {why}. Nothing was merged or removed and the work is still in the task's worktree.")

    def task_finish(self, task_id: str, *, action: str = "discard") -> dict[str, Any]:
        """Finish an isolated task's worktree: 'merge' fast-forwards the base to the task
        branch then removes the worktree; 'discard' throws the worktree + branch away (after a
        best-effort work-preservation checkpoint, so discarded work can still be salvaged)."""
        if action not in {"merge", "discard"}:
            raise PolicyError("finish action must be merge or discard")
        task = self._read_task(task_id)
        worktree = task.get("worktree")
        if not worktree:
            raise PolicyError("task has no worktree to finish")
        if task.get("worktree_finished"):
            raise PolicyError("task worktree is already finished")
        base = self._workspace(worktree["base"], write=True)
        branch = worktree["branch"]
        merged = False
        if action == "merge":
            # A merge carries COMMITS; the worktree is then removed with --force. Work not yet committed was deleted
            # with it while the reply said merged. So the work
            # is committed first (a checkpoint), and if it cannot be, nothing is merged and nothing is removed.
            wt_ws = self.workspaces.get(worktree["workspace"])
            if wt_ws is None and Path(worktree["path"]).is_dir():
                self._register_worktree_workspace(base, worktree["workspace"], Path(worktree["path"]).resolve(),
                                                  branch, task_id)
                wt_ws = self.workspaces.get(worktree["workspace"])
            wt_head = None
            if wt_ws is not None:
                try:
                    dirty = self._uncommitted(wt_ws)
                except RunnerError as exc:
                    raise ConflictError(f"{exc}; nothing was merged or removed") from None
                if dirty:
                    try:
                        self.task_checkpoint(task_id, note="saved before merge: work that was not yet committed")
                    except Exception as exc:                             # noqa: BLE001 - refused below, nothing lost
                        raise ConflictError(f"the worktree holds uncommitted work and it could not be saved ({exc}); "
                                            "nothing was merged or removed") from None
                    left = self._uncommitted(wt_ws)
                    if left:
                        raise ConflictError(f"the worktree still holds {len(left)} uncommitted file(s) a merge would "
                                            "delete; nothing was merged or removed")
                    task = self._read_task(task_id)
                head = self._git(wt_ws, ["rev-parse", "HEAD"])
                wt_head = head["stdout"].strip() if head["returncode"] == 0 else None
                if not wt_head:
                    raise ConflictError("git could not name the worktree's last commit; nothing was merged or removed")
            with self._merge_lock:
                self._merge_into_base(base, branch)
            # the worktree is removed with --force below: only once the base is SEEN to hold its last commit
            if wt_head and self._git(base, ["merge-base", "--is-ancestor", wt_head, "HEAD"])["returncode"] != 0:
                raise ConflictError(f"{base.id} does not hold the worktree's last commit {wt_head[:10]} after the "
                                    "merge; the worktree was not removed")
            if wt_ws is not None:
                try:
                    carried = self._carry_ignored(wt_ws, base)
                except Exception as exc:                                 # noqa: BLE001 - refused, nothing removed
                    raise ConflictError(f"the merge landed but the worktree's ignored files could not be carried "
                                        f"({exc}); the worktree was not removed") from None
                if carried:
                    self._audit("worktree_carried_ignored", base.id, {"task_id": task_id, "files": carried[:200],
                                                                      "count": len(carried)})
            merged = True
        # Discard is irreversible; capture the work first (merge already preserves it in base).
        preserved_checkpoint = self._preserve_before_discard(task, worktree) if action == "discard" else None
        self._git(base, ["worktree", "remove", "--force", worktree["path"]])
        self._git(base, ["branch", "-D", branch])
        self.workspaces.pop(worktree["workspace"], None)
        task["worktree_finished"] = {"action": action, "merged": merged, "at": _utc_now(),
                                     "preserved_checkpoint": preserved_checkpoint}
        task["updated_at"] = _utc_now()
        self._cascade_escalation_on_task_end(task, task_id, f"worktree {action}")
        self._write_task(task)
        self._audit("worktree_finish", base.id,
                    {"task_id": task_id, "action": action, "merged": merged,
                     "preserved_checkpoint": preserved_checkpoint})
        return {"task_id": task_id, "action": action, "merged": merged, "base": base.id,
                "branch": branch, "preserved_checkpoint": preserved_checkpoint}

    def _latest_checkpoint(self, task_id: str) -> dict[str, Any] | None:
        target = self.checkpoints_dir / task_id
        if not target.is_dir():
            return None
        files = sorted(target.glob("*.json"), key=lambda p: p.stat().st_mtime)
        if not files:
            return None
        cp = json.loads(files[-1].read_text(encoding="utf-8"))
        test = cp.get("test_result") if isinstance(cp.get("test_result"), dict) else {}
        return {
            "id": cp.get("id"),
            "created_at": cp.get("created_at"),
            "note": cp.get("note"),
            "git_commit": cp.get("git_commit"),
            "test_profile": cp.get("test_profile"),
            "test_returncode": (test or {}).get("returncode"),
        }

    def task_resume(self, task_id: str, *, chat_id: str | None = None) -> dict[str, Any]:
        """Rehydrate a task so a fresh/compacted context can continue it. Re-attaches the
        worktree workspace if a runner restart dropped its in-memory registration, reactivates
        a paused/cancelled/blocked task, and returns a continuation brief (task plan, remaining
        steps, latest checkpoint, current Git status)."""
        task = self._read_task(task_id)
        reattached = False
        worktree = task.get("worktree")
        if worktree and not task.get("worktree_finished"):
            wt_id = worktree["workspace"]
            if wt_id not in self.workspaces:
                wt_root = Path(worktree["path"])
                base = self.workspaces.get(worktree["base"])
                if base and wt_root.is_dir() and (wt_root / ".git").exists():
                    self._register_worktree_workspace(
                        base, wt_id, wt_root.resolve(), worktree["branch"], task_id
                    )
                    reattached = True
        if chat_id:
            task["chat_id"] = chat_id       # picked up in another chat: that chat is told about it from here on
        if task.get("status") in {"paused", "cancelled", "blocked"}:
            task["status"] = "active"
        task["notes"].append({"timestamp": _utc_now(), "text": "resumed"})
        task["updated_at"] = _utc_now()
        self._write_task(task)
        active_workspace = task["workspace"]
        git_status = None
        workspace = self.workspaces.get(active_workspace)
        if workspace and workspace.git and (workspace.root / ".git").exists():
            git_status = self.git_status(active_workspace)
        remaining = [s for s in task.get("plan", []) if s.get("status") != "completed"]
        self._audit("task_resume", active_workspace, {"task_id": task_id, "worktree_reattached": reattached})
        return {
            "task": self._with_progress(task),
            "active_workspace": active_workspace,
            "worktree_reattached": reattached,
            "latest_checkpoint": self._latest_checkpoint(task_id),
            "git_status": git_status,
            "remaining_plan": remaining,
        }

    def task_get(self, task_id: str) -> dict[str, Any]:
        task = self._read_task(task_id)
        self._audit("task_get", task["workspace"], {"task_id": task_id})
        return self._with_progress(task)

    def _auto_cancel_stale_tasks(self) -> None:
        """Auto-cancel standalone active tasks idle beyond TASK_AUTO_CANCEL_SECONDS so a forgotten/abandoned
        task does not linger as 'active' forever (Phase-5 auto-close, safe form). Project-linked tasks are
        left to the project lifecycle."""
        threshold = self.task_auto_cancel_seconds
        if threshold <= 0:
            return
        linked = set()
        for path in self.projects_dir.glob("*.json"):
            try:
                pr = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if pr.get("status") not in {"complete", "cancelled"}:
                for node in pr.get("nodes", []):
                    if node.get("task_id"):
                        linked.add(node["task_id"])
        now = datetime.now(timezone.utc)
        for path in self.tasks_dir.glob("*.json"):
            try:
                task = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if task.get("status") != "active" or task.get("id") in linked:
                continue
            updated = _parse_utc(task.get("updated_at"))
            if updated is None:
                continue
            age = (now - updated).total_seconds()
            if age > threshold:
                task["status"] = "cancelled"
                task["updated_at"] = _utc_now()
                task.setdefault("notes", []).append(
                    {"timestamp": _utc_now(), "text": f"auto-cancelled: stale (idle {int(age)}s > {threshold}s)"})
                self._write_task(task)
                self._audit("task_auto_cancel", task.get("workspace"),
                            {"task_id": task.get("id"), "idle_seconds": int(age)})
    def task_list(self, *, include_finished: bool = False) -> dict[str, Any]:
        self._auto_cancel_stale_tasks()
        tasks = []
        for path in self.tasks_dir.glob("*.json"):
            try:
                task = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            finished = task.get("status") in {"complete", "cancelled"} or bool(task.get("worktree_finished"))
            if finished and not include_finished:
                continue
            progress = self._task_progress(task)
            tasks.append({
                "id": task.get("id"),
                "objective": task.get("objective"),
                "status": task.get("status"),
                "workspace": task.get("workspace"),
                "updated_at": task.get("updated_at"),
                "isolated": bool(task.get("worktree")),
                "owner": task.get("owner"),
                "chat_id": task.get("chat_id"),
                "current_phase": progress["current_phase"],
                "last_activity_at": progress["last_activity_at"],
                "seconds_since_update": progress["seconds_since_update"],
                "needs_attention": progress["needs_attention"],
                "snag_reason": progress["snag_reason"],
                "status_line": progress["status_line"],
            })
        tasks.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
        self._audit("task_list", None, {"count": len(tasks), "include_finished": include_finished})
        return {"tasks": tasks[:50]}

    def _with_progress(self, task: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(task)
        enriched["progress"] = self._task_progress(task)
        return enriched

    def _task_progress(self, task: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        created = _parse_utc(task.get("created_at"))
        updated = _parse_utc(task.get("updated_at"))
        notes = task.get("notes") if isinstance(task.get("notes"), list) else []
        latest_note = next((note for note in reversed(notes) if isinstance(note, dict)), None)
        latest_note_at = _parse_utc(latest_note.get("timestamp")) if latest_note else None
        last_activity_at = latest_note_at or updated or created
        last_activity_text = (latest_note.get("text") if latest_note else None) or "Task updated"

        current_phase = None
        for item in task.get("plan", []):
            if isinstance(item, dict) and item.get("status") == "in_progress":
                current_phase = item.get("step")
                break
        if not current_phase:
            remaining = [
                item.get("step")
                for item in task.get("plan", [])
                if isinstance(item, dict) and item.get("status") == "pending"
            ]
            current_phase = remaining[0] if remaining else str(task.get("status") or "unknown")

        status = task.get("status")
        seconds_since_update = int((now - (updated or now)).total_seconds())
        elapsed_seconds = int((now - (created or now)).total_seconds())
        stale_after_seconds = 600
        snag_after_seconds = 1800
        snag_reason = None
        if status in {"blocked", "cancelled"}:
            snag_reason = f"task status is {status}"
        elif status == "active" and seconds_since_update >= snag_after_seconds:
            snag_reason = f"no update for {seconds_since_update} seconds"
        needs_attention = bool(snag_reason) or (status == "active" and seconds_since_update >= stale_after_seconds)

        if snag_reason:
            status_line = f"{status}: {current_phase}; needs attention ({snag_reason})"
        elif status == "active" and seconds_since_update >= stale_after_seconds:
            status_line = f"{status}: {current_phase}; stale for {seconds_since_update} seconds"
        else:
            status_line = f"{status}: {current_phase}; last update {seconds_since_update} seconds ago"

        return {
            "current_phase": current_phase,
            "last_activity_at": last_activity_at.isoformat() if last_activity_at else None,
            "last_activity_text": last_activity_text,
            "seconds_since_update": seconds_since_update,
            "elapsed_seconds": elapsed_seconds,
            "stale_after_seconds": stale_after_seconds,
            "snag_after_seconds": snag_after_seconds,
            "needs_attention": needs_attention,
            "snag_reason": snag_reason,
            "status_line": status_line,
        }

    def task_update(
        self,
        task_id: str,
        *,
        plan: list[dict[str, str]] | None = None,
        note: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        task = self._read_task(task_id)
        if plan is not None:
            for item in plan:
                if item.get("status") not in {"pending", "in_progress", "completed"} or not item.get("step"):
                    raise PolicyError("invalid plan item")
            if sum(item["status"] == "in_progress" for item in plan) > 1:
                raise PolicyError("at most one plan item may be in progress")
            task["plan"] = plan
        if note:
            task["notes"].append({"timestamp": _utc_now(), "text": note})
        if status is not None:
            if status not in {"active", "complete", "blocked", "paused", "cancelled"}:
                raise PolicyError("invalid task status")
            task["status"] = status
            if status in {"complete", "cancelled"}:
                self._cascade_escalation_on_task_end(task, task_id, f"task {status}")
        task["updated_at"] = _utc_now()
        self._write_task(task)
        self._audit("task_update", task["workspace"], {"task_id": task_id, "status": task["status"]})
        return task

    @staticmethod
    def _decision_risk(question: str, options: list[dict[str, str]]) -> tuple[str, list[str]]:
        text = " ".join([question, *(str(value) for option in options for value in option.values())]).lower()
        high = {
            "authentication or credentials": ("auth", "credential", "secret", "token", "password"),
            "privileged infrastructure boundary": ("docker socket", "compose", "sudo", "root", "privileged"),
            "destructive or irreversible change": ("delete", "destructive", "irreversible", "migration", "drop "),
            "external publication or access": ("github", "push", "public", "external service", "internet access"),
        }
        medium = {
            "architecture boundary": ("architecture", "sidecar", "service boundary", "process boundary"),
            "new dependency or persistent schema": ("dependency", "package", "database", "schema", "storage"),
            "public API or compatibility": ("api", "compatibility", "breaking change"),
        }
        reasons = [label for label, words in high.items() if any(word in text for word in words)]
        if reasons:
            return "HIGH", reasons
        reasons = [label for label, words in medium.items() if any(word in text for word in words)]
        return ("MED", reasons) if reasons else ("LOW", ["scoped and reversible implementation choice"])

    def decision_create(
        self, task_id: str, question: str, options: list[dict[str, str]], recommendation: str,
        *, rationale: str = "", reversible: bool = True,
    ) -> dict[str, Any]:
        task = self._read_task(task_id)
        if task.get("status") in {"complete", "cancelled"}:
            raise ConflictError("cannot create a decision for a finished task")
        for path in self.decisions_dir.glob("*.json"):
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing.get("task_id") == task_id and existing.get("status") == "pending":
                return existing
        question = question.strip()
        if not question or not (2 <= len(options) <= 4):
            raise PolicyError("decision needs a question and 2-4 options")
        clean, ids = [], set()
        for raw in options:
            option_id = str(raw.get("id", "")).strip()
            label = str(raw.get("label", "")).strip()
            tradeoff = str(raw.get("tradeoff", "")).strip()
            if (not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,31}", option_id)
                    or option_id in ids or not label or not tradeoff):
                raise PolicyError(f"invalid decision option: {option_id!r}")
            ids.add(option_id)
            clean.append({"id": option_id, "label": label, "tradeoff": tradeoff})
        if recommendation not in ids:
            raise PolicyError("recommendation must match an option id")
        risk, risk_reasons = self._decision_risk(question, clean)
        decision = {
            "id": uuid.uuid4().hex, "task_id": task_id, "workspace": task["workspace"],
            "question": question, "options": clean, "recommendation": recommendation,
            "rationale": rationale.strip(), "reversible": bool(reversible), "risk": risk,
            "risk_reasons": risk_reasons, "status": "pending", "choice": None,
            "created_at": _utc_now(), "resolved_at": None,
        }
        (self.decisions_dir / f"{decision['id']}.json").write_text(
            json.dumps(decision, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        task["status"] = "blocked"
        task["blocked_by_decision"] = decision["id"]
        task.setdefault("notes", []).append({"timestamp": _utc_now(),
            "text": f"Awaiting decision {decision['id']}: {question}"})
        task["updated_at"] = _utc_now()
        self._write_task(task)
        self._audit("decision_create", task["workspace"],
                    {"task_id": task_id, "decision_id": decision["id"], "risk": risk})
        return decision

    def decision_get(self, decision_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[0-9a-f]{32}", decision_id):
            raise PolicyError("invalid decision id")
        path = self.decisions_dir / f"{decision_id}.json"
        if not path.exists():
            raise PolicyError("decision not found")
        return json.loads(path.read_text(encoding="utf-8"))

    def decision_list(self, *, include_resolved: bool = False) -> dict[str, Any]:
        decisions = []
        for path in self.decisions_dir.glob("*.json"):
            try:
                decision = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if include_resolved or decision.get("status") == "pending":
                decisions.append(decision)
        decisions.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return {"decisions": decisions}

    def decision_resolve(self, decision_id: str, choice: str, *, note: str = "") -> dict[str, Any]:
        decision = self.decision_get(decision_id)
        if decision["status"] != "pending":
            raise ConflictError("decision is already resolved")
        if choice not in {option["id"] for option in decision["options"]}:
            raise PolicyError("choice must match an option id")
        decision["status"] = "resolved"
        decision["choice"] = choice
        decision["resolution_note"] = note.strip()
        decision["resolved_at"] = _utc_now()
        (self.decisions_dir / f"{decision_id}.json").write_text(
            json.dumps(decision, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        task = self._read_task(decision["task_id"])
        if task.get("blocked_by_decision") == decision_id:
            task.pop("blocked_by_decision", None)
            task["status"] = "active"
            task.setdefault("notes", []).append({"timestamp": _utc_now(),
                "text": f"Decision {decision_id} resolved: {choice}. {note.strip()}".strip()})
            task["updated_at"] = _utc_now()
            self._write_task(task)
        self._audit("decision_resolve", decision["workspace"],
                    {"task_id": decision["task_id"], "decision_id": decision_id, "choice": choice})
        return decision
    def escalation_packet_create(
        self, task_id: str, reason: str, requested_help: str, *, suggested_agent: str | None = None,
    ) -> dict[str, Any]:
        task = self._read_task(task_id)
        allowed_reasons = {"architecture_or_security", "repeated_failure", "capability_limit",
                           "external_research", "user_requested"}
        if reason not in allowed_reasons:
            raise PolicyError("invalid escalation reason")
        if suggested_agent not in {None, "claude", "codex"}:
            raise PolicyError("suggested agent must be claude or codex")
        requested_help = requested_help.strip()
        if not requested_help:
            raise PolicyError("requested_help is required")
        workspace_id = task["workspace"]
        project = None
        for path in self.projects_dir.glob("*.json"):
            candidate = json.loads(path.read_text(encoding="utf-8"))
            if any(node.get("task_id") == task_id for node in candidate.get("nodes", [])):
                project = candidate
                break
        decisions = [item for item in self.decision_list(include_resolved=True)["decisions"]
                     if item.get("task_id") == task_id]
        checkpoints = []
        checkpoint_dir = self.checkpoints_dir / task_id
        if checkpoint_dir.exists():
            for path in sorted(checkpoint_dir.glob("*.json"), reverse=True)[:3]:
                try:
                    checkpoint = json.loads(path.read_text(encoding="utf-8"))
                    checkpoints.append({key: checkpoint.get(key) for key in
                                        ("id", "created_at", "note", "git_commit", "test_profile")})
                except (OSError, json.JSONDecodeError):
                    continue
        status = self.git_status(workspace_id)
        diff = self.git_diff(workspace_id)
        packet_id = uuid.uuid4().hex
        constraints = [
            "local-only Git; no push or external publication",
            "no deployment or production writes without the existing two approval gates (%s)" % _OWNER,
            "orchestrator Python and tests only; compose files and secrets remain protected",
            "preserve unrelated changes and return concrete verification evidence",
        ]
        packet = {
            "id": packet_id, "status": "ready", "sent": False,
            "requires_user_agent_choice": True, "suggested_agent": suggested_agent,
            "selected_agent": None, "use_mode": None, "resolved_at": None,
            "reason": reason, "requested_help": requested_help, "task_id": task_id,
            "workspace": workspace_id, "project_id": project.get("id") if project else None,
            "objective": task.get("objective"), "acceptance_criteria": task.get("acceptance_criteria", []),
            "task_status": task.get("status"), "plan": task.get("plan", []),
            "recovery": task.get("recovery"), "decisions": decisions,
            "checkpoints": checkpoints, "git_status": status, "git_diff": diff,
            "constraints": constraints, "created_at": _utc_now(),
        }
        lines = [
            "SUPERVISED LOCAL CODING JOB", "", f"Packet ID: {packet_id}",
            f"Suggested agent: {suggested_agent or (_OWNER + ' chooses Claude or Codex')}",
            f"Escalation reason: {reason}", "", "Objective", str(packet["objective"]), "",
            "Requested help", requested_help, "", "Authoritative state",
            f"- task_id: {task_id}", f"- project_id: {packet['project_id'] or 'none'}",
            f"- workspace: {workspace_id}", f"- task status: {packet['task_status']}", "",
            "Acceptance criteria", *(f"- {item}" for item in packet["acceptance_criteria"]), "",
            "Constraints", *(f"- {item}" for item in constraints), "", "Evidence",
            f"- recovery: {json.dumps(packet['recovery'], ensure_ascii=True)}",
            f"- decisions: {json.dumps(decisions, ensure_ascii=True)}",
            f"- checkpoints: {json.dumps(checkpoints, ensure_ascii=True)}",
            f"- git status: {status.get('stdout', '').strip() or 'clean'}", "",
            "Current diff", "```diff", diff.get("stdout", ""), "```", "",
            "Return contract", "Return findings/plan or a scoped patch with tests; do not deploy or push.",
        ]
        packet["handoff"] = "\n".join(lines)
        (self.escalations_dir / f"{packet_id}.json").write_text(
            json.dumps(packet, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        task["status"] = "blocked"
        task["blocked_by_escalation"] = packet_id
        task.setdefault("notes", []).append({"timestamp": _utc_now(),
            "text": f"Escalation packet {packet_id} ready; awaiting the agent choice ({_OWNER})."})
        task["updated_at"] = _utc_now()
        self._write_task(task)
        self._audit("escalation_packet_create", workspace_id,
                    {"packet_id": packet_id, "task_id": task_id, "reason": reason})
        return packet

    def escalation_packet_get(self, packet_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[0-9a-f]{32}", packet_id):
            raise PolicyError("invalid escalation packet id")
        path = self.escalations_dir / f"{packet_id}.json"
        if not path.exists():
            raise PolicyError("escalation packet not found")
        return json.loads(path.read_text(encoding="utf-8"))

    def escalation_packet_select(self, packet_id: str, agent: str, use_mode: str) -> dict[str, Any]:
        packet = self.escalation_packet_get(packet_id)
        if packet["status"] != "ready":
            raise ConflictError("escalation packet is not awaiting agent choice")
        if agent not in {"claude", "codex"}:
            raise PolicyError("agent must be claude or codex")
        if use_mode not in {"direct", "interactive", "background_review", "dress_to_impress"}:
            raise PolicyError("invalid external use mode")
        packet["status"] = "selected"
        packet["selected_agent"] = agent
        packet["use_mode"] = use_mode
        packet["requires_user_agent_choice"] = False
        path = self.escalations_dir / f"{packet_id}.json"
        path.write_text(json.dumps(packet, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        self._audit("escalation_packet_select", packet["workspace"],
                    {"packet_id": packet_id, "agent": agent, "use_mode": use_mode})
        return packet

    def escalation_packet_resolve(self, packet_id: str, outcome: str, *, note: str = "") -> dict[str, Any]:
        packet = self.escalation_packet_get(packet_id)
        if packet["status"] not in {"ready", "selected"}:
            raise ConflictError("escalation packet is already resolved")
        if outcome not in {"returned", "cancelled"}:
            raise PolicyError("outcome must be returned or cancelled")
        packet["status"] = "resolved" if outcome == "returned" else "cancelled"
        packet["outcome"] = outcome
        packet["resolution_note"] = note.strip()
        packet["resolved_at"] = _utc_now()
        path = self.escalations_dir / f"{packet_id}.json"
        path.write_text(json.dumps(packet, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        task = self._read_task(packet["task_id"])
        if task.get("blocked_by_escalation") == packet_id:
            task.pop("blocked_by_escalation", None)
            task["status"] = "active"
            task.setdefault("notes", []).append({"timestamp": _utc_now(),
                "text": f"Escalation {packet_id} {outcome}. {note.strip()}".strip()})
            task["updated_at"] = _utc_now()
            self._write_task(task)
        self._audit("escalation_packet_resolve", packet["workspace"],
                    {"packet_id": packet_id, "outcome": outcome})
        return packet

    def _cascade_escalation_on_task_end(self, task: dict[str, Any], task_id: str, reason: str) -> None:
        """Close the escalation packet still blocking a task that has just reached a terminal state.

        A packet is created "ready" and linked via task["blocked_by_escalation"], but only
        select/resolve ever transitioned it — so a task that ended by abandon/finish/complete left its
        packet "ready" forever and the desk kept re-DMing it. This closes
        it from every terminal path. It must NOT reuse escalation_packet_resolve: that path REACTIVATES
        the task (status->active) for the human-return case, which would undo the very termination we
        are reacting to. Best-effort — a missing or already-terminal packet is a no-op and nothing here
        may raise. Mutates `task` (drops the link); the CALLER persists it, so there is no double write.
        """
        packet_id = task.get("blocked_by_escalation")
        if not packet_id:
            return
        try:
            packet = self.escalation_packet_get(packet_id)
        except PolicyError:
            task.pop("blocked_by_escalation", None)   # dangling link to a packet that is gone
            return
        if packet.get("status") in {"ready", "selected"}:
            packet["status"] = "cancelled"
            packet["outcome"] = "task_ended"
            packet["resolution_note"] = reason
            packet["resolved_at"] = _utc_now()
            try:
                (self.escalations_dir / f"{packet_id}.json").write_text(
                    json.dumps(packet, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
                self._audit("escalation_packet_cascade", packet.get("workspace"),
                            {"packet_id": packet_id, "task_id": task_id, "reason": reason})
            except OSError:
                pass
        task.pop("blocked_by_escalation", None)

    def escalation_packet_list(self) -> dict[str, Any]:
        packets = []
        for path in self.escalations_dir.glob("*.json"):
            try:
                packets.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        packets.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return {"escalations": packets}
    def task_checkpoint(
        self,
        task_id: str,
        *,
        test_profile: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        task = self._read_task(task_id)
        workspace_id = task["workspace"]
        status = self.git_status(workspace_id)
        diff = self.git_diff(workspace_id)
        test_result = self.test_run(workspace_id, test_profile) if test_profile else None
        checkpoint_id = uuid.uuid4().hex
        # Capture a real restore point: commit the current (non-protected) working state
        # to the workspace's own local history. Local only — never pushed.
        git_commit = self._commit_checkpoint(
            workspace_id, f"checkpoint {checkpoint_id}: {(note or '').strip()[:200]}"
        )
        checkpoint = {
            "id": checkpoint_id,
            "task_id": task_id,
            "workspace": workspace_id,
            "created_at": _utc_now(),
            "note": (note or "")[:2_000],
            "task": task,
            "git_status": status,
            "git_diff": diff,
            "git_commit": git_commit,
            "test_profile": test_profile,
            "test_result": test_result,
        }
        target_dir = self.checkpoints_dir / task_id
        target_dir.mkdir(exist_ok=True)
        path = target_dir / f"{checkpoint_id}.json"
        path.write_text(json.dumps(checkpoint, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        self._audit(
            "task_checkpoint",
            workspace_id,
            {"task_id": task_id, "checkpoint_id": checkpoint_id, "test_profile": test_profile},
        )
        return {
            "id": checkpoint_id,
            "task_id": task_id,
            "workspace": workspace_id,
            "created_at": checkpoint["created_at"],
            "note": checkpoint["note"],
            "git_commit": git_commit,
            "test_profile": test_profile,
            "test_returncode": test_result.get("returncode") if test_result else None,
            "diff_bytes": len(diff.get("stdout", "").encode("utf-8")),
        }

    def _commit_checkpoint(self, workspace_id: str, message: str) -> str | None:
        """Commit the current non-protected working state to the workspace's local Git
        history and return the commit SHA. Returns None for non-Git workspaces. Local
        only — never pushes. Protected paths are unstaged so secrets are never committed."""
        workspace = self._workspace(workspace_id, write=True)
        if not workspace.git or not (workspace.root / ".git").exists():
            return None
        # A checkpoint is a restore point: one git could not make is said, never returned as an empty commit id.
        added = self._git(workspace, ["add", "-A"])
        if added["returncode"] != 0:
            raise ConflictError("the checkpoint could not stage the work (%s)" % (added["stderr"].strip()[:200],))
        for prefix in workspace.protected_paths:
            # Unstage protected paths if present; a missing pathspec is a harmless no-op.
            self._git(workspace, ["reset", "-q", "--", prefix])
        done = self._git(workspace, [*GIT_IDENTITY, "commit", "--allow-empty", "-q", "-m", message])
        if done["returncode"] != 0:
            raise ConflictError("the checkpoint could not be committed (%s)" % (
                (done["stderr"] or done["stdout"]).strip()[:200],))
        head = self._git(workspace, ["rev-parse", "HEAD"])
        if head["returncode"] != 0 or not head["stdout"].strip():
            raise ConflictError("the checkpoint's commit could not be read back (%s)" % (head["stderr"].strip()[:200],))
        return head["stdout"].strip()

    def task_restore(self, task_id: str, checkpoint_id: str) -> dict[str, Any]:
        """Restore a workspace to a checkpoint's committed state. Hard-resets tracked files
        to the checkpoint commit, then removes untracked files created since — while
        preserving protected and Git-ignored paths."""
        checkpoint = self.task_checkpoint_get(task_id, checkpoint_id)
        workspace_id = checkpoint["workspace"]
        commit = checkpoint.get("git_commit")
        self._preserve_before_reset(dict(self._read_task(task_id), workspace=workspace_id), target=commit,
                                    why="a restore to checkpoint %s" % checkpoint_id)
        restored = self._restore_workspace_commit(workspace_id, commit)
        self._audit(
            "task_restore",
            workspace_id,
            {"task_id": task_id, "checkpoint_id": checkpoint_id, "commit": commit},
        )
        return {
            "task_id": task_id,
            "checkpoint_id": checkpoint_id,
            "restored_commit": commit,
            "head": restored["head"],
            "git_status": restored["git_status"],
        }

    def task_checkpoint_get(self, task_id: str, checkpoint_id: str) -> dict[str, Any]:
        self._task_path(task_id)
        if not re.fullmatch(r"[0-9a-f]{32}", checkpoint_id):
            raise PolicyError("invalid checkpoint id")
        path = self.checkpoints_dir / task_id / f"{checkpoint_id}.json"
        if not path.exists():
            raise PolicyError("checkpoint not found")
        checkpoint = json.loads(path.read_text(encoding="utf-8"))
        self._audit(
            "task_checkpoint_get",
            checkpoint["workspace"],
            {"task_id": task_id, "checkpoint_id": checkpoint_id},
        )
        return checkpoint

    def _task_path(self, task_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", task_id):
            raise PolicyError("invalid task id")
        return self.tasks_dir / f"{task_id}.json"

    def _active_project_for_workspace(self, workspace_id: str) -> dict[str, Any] | None:
        for path in self.projects_dir.glob("*.json"):
            try:
                project = self._sync_project(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError, PolicyError):
                continue
            if (project.get("workspace") == workspace_id
                    and project.get("status") not in {"complete", "cancelled"}):
                return project
        return None
    def project_start(self, workspace_id: str, objective: str, nodes: list[dict[str, Any]]) -> dict[str, Any]:
        self._workspace(workspace_id, write=True)
        active = self._active_project_for_workspace(workspace_id)
        if active:
            raise ConflictError(f"active project already exists for {workspace_id}: {active['id']}")
        if not objective.strip() or not nodes or len(nodes) > 50:
            raise PolicyError("project needs an objective and 1-50 task nodes")
        clean, ids = [], set()
        for raw in nodes:
            node_id, node_objective = str(raw.get("id", "")).strip(), str(raw.get("objective", "")).strip()
            if (not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", node_id)
                    or node_id in ids or not node_objective):
                raise PolicyError(f"invalid project node: {node_id!r}")
            ids.add(node_id)
            clean.append({"id": node_id, "objective": node_objective,
                          "acceptance_criteria": [str(x) for x in raw.get("acceptance_criteria", [])],
                          "depends_on": [str(x) for x in raw.get("depends_on", [])],
                          "status": "pending", "task_id": None})
        for node in clean:
            if node["id"] in node["depends_on"] or any(dep not in ids for dep in node["depends_on"]):
                raise PolicyError(f"invalid dependency for node {node['id']}")
        done, remaining = set(), {node["id"]: set(node["depends_on"]) for node in clean}
        while remaining:
            ready = [node_id for node_id, deps in remaining.items() if deps <= done]
            if not ready:
                raise PolicyError("project task graph contains a dependency cycle")
            for node_id in ready:
                done.add(node_id); remaining.pop(node_id)
        project = {"id": uuid.uuid4().hex, "workspace": workspace_id,
                   "objective": objective.strip(), "status": "active", "nodes": clean,
                   "current_node": None, "created_at": _utc_now(), "updated_at": _utc_now()}
        self._write_project(project)
        self._audit("project_start", workspace_id, {"project_id": project["id"], "nodes": len(clean)})
        return project

    def _sync_project(self, project: dict[str, Any]) -> dict[str, Any]:
        changed = False
        for node in project["nodes"]:
            if not node.get("task_id"):
                continue
            task = self._read_task(node["task_id"])
            mapped = "complete" if task.get("status") == "complete" else (
                "blocked" if task.get("status") in {"blocked", "cancelled"} else "active")
            if node["status"] != mapped:
                node["status"], changed = mapped, True
        statuses = {node["status"] for node in project["nodes"]}
        new_status = ("complete" if statuses == {"complete"} else "blocked" if "blocked" in statuses
                      else project["status"] if project["status"] in {"paused", "cancelled"} else "active")
        if project["status"] != new_status:
            project["status"], changed = new_status, True
        if changed:
            project["updated_at"] = _utc_now(); self._write_project(project)
        return project

    def project_advance(self, project_id: str) -> dict[str, Any]:
        project = self._sync_project(self._read_project(project_id))
        if project["status"] in {"paused", "cancelled", "complete", "blocked"}:
            return project
        active = next((node for node in project["nodes"] if node["status"] == "active"), None)
        if active:
            project["current_node"] = active["id"]; return project
        done = {node["id"] for node in project["nodes"] if node["status"] == "complete"}
        ready = next((node for node in project["nodes"]
                      if node["status"] == "pending" and set(node["depends_on"]) <= done), None)
        if not ready:
            project["status"], project["current_node"] = "complete", None
        else:
            task = self.task_start(project["workspace"], ready["objective"],
                                   acceptance_criteria=ready["acceptance_criteria"], isolate=False, _internal=True)
            ready["task_id"], ready["status"] = task["id"], "active"
            project["current_node"] = ready["id"]
        project["updated_at"] = _utc_now(); self._write_project(project)
        self._audit("project_advance", project["workspace"], {"project_id": project_id,
                    "node": project.get("current_node")})
        return project

    def project_resume(self, project_id: str) -> dict[str, Any]:
        project = self._sync_project(self._read_project(project_id))
        blocked = next((node for node in project["nodes"] if node["status"] == "blocked"), None)
        if blocked and blocked.get("task_id"):
            self.task_resume(blocked["task_id"]); blocked["status"] = "active"
            project["current_node"] = blocked["id"]
        project["status"] = "active"; project["updated_at"] = _utc_now()
        self._write_project(project); return project

    def project_get(self, project_id: str) -> dict[str, Any]:
        return self._sync_project(self._read_project(project_id))

    def project_list(self, include_finished: bool = False) -> dict[str, Any]:
        projects = []
        for path in self.projects_dir.glob("*.json"):
            try: project = self._sync_project(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError, PolicyError): continue
            if include_finished or project["status"] not in {"complete", "cancelled"}: projects.append(project)
        return {"projects": sorted(projects, key=lambda x: x["updated_at"], reverse=True)[:25]}

    def _project_path(self, project_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", project_id or ""):
            raise PolicyError("invalid project id")
        return self.projects_dir / f"{project_id}.json"

    def _read_project(self, project_id: str) -> dict[str, Any]:
        path = self._project_path(project_id)
        if not path.exists(): raise PolicyError("project not found")
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_project(self, project: dict[str, Any]) -> None:
        path = self._project_path(project["id"]); temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(project, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        temporary.replace(path)
    def _read_task(self, task_id: str) -> dict[str, Any]:
        path = self._task_path(task_id)
        if not path.exists():
            raise PolicyError("task not found")
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_task(self, task: dict[str, Any]) -> None:
        path = self._task_path(task["id"])
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(task, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        temporary.replace(path)

    @staticmethod
    def _session_usage(session: int) -> tuple:
        """(CPU ticks used so far, resident bytes now) across every process in this session, finished children's CPU included (Linux /proc)."""
        ticks, resident = 0, 0
        page = os.sysconf("SC_PAGE_SIZE")
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            try:
                with open(f"/proc/{entry}/stat", encoding="latin-1") as f:
                    fields = f.read().rpartition(")")[2].split()
            except OSError:
                continue
            if int(fields[3]) == session:                       # session id; fields start after the command name
                ticks += sum(int(x) for x in fields[11:15])     # utime stime cutime cstime
                resident += int(fields[21]) * page              # rss
        return ticks, resident

    def _run_watched(self, argv: list[str], cwd: Path, env: dict, timeout: int, idle: int, memory_gb: float) -> tuple:
        """Runs until it finishes or crosses a limit. -> (stdout, stderr, returncode, why it was ended or None). Output goes to files, so a leftover child
        holding a pipe cannot hang the run and a stopped run keeps everything it wrote. A run that crosses the memory limit gets SIGABRT first: with
        PYTHONFAULTHANDLER set, Python writes the line it is on to stderr, which is the fix's other half (the job sees WHERE, not only that it died)."""
        import signal
        import tempfile
        import time as _time
        limit = int(memory_gb * 1024 ** 3) if memory_gb else None
        env = dict(env, PYTHONFAULTHANDLER="1")
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err, shell=False, start_new_session=True)
            started = last_change = _time.monotonic()
            seen, why = None, None
            while proc.poll() is None:
                _time.sleep(self.test_poll_seconds)
                now = _time.monotonic()
                try:
                    ticks, resident = self._session_usage(proc.pid)
                    state = (ticks, os.fstat(out.fileno()).st_size, os.fstat(err.fileno()).st_size)
                except OSError:
                    state, resident = seen, 0
                if limit and resident > limit:
                    why = ("it used over %g GB of memory, so it was stopped; stderr ends with a stack dump of the line it was on. "
                           "A list or loop that keeps growing is the usual cause" % memory_gb)
                    try:
                        os.killpg(proc.pid, signal.SIGABRT)
                    except OSError:
                        pass
                    _time.sleep(max(self.test_poll_seconds, 1.0))     # time to write the dump
                if state != seen:
                    seen, last_change = state, now
                elif idle and now - last_change >= idle:
                    why = why or "no CPU used and no output for %d s: it had stopped working" % idle
                if now - started >= timeout:
                    why = why or "timed out after %d s" % timeout
                if why:
                    break
            try:
                os.killpg(proc.pid, signal.SIGKILL)               # the whole session: a finished run's leftovers go too
            except OSError:
                pass
            proc.wait()
            out.seek(0)
            err.seek(0)
            return out.read(), err.read(), (None if why else proc.returncode), why

    def _run(self, argv: list[str], cwd: Path, *, timeout: int, idle: int | None = None, memory_gb: float | None = None) -> dict[str, Any]:
        if not argv or any(not isinstance(part, str) or "\x00" in part for part in argv):
            raise PolicyError("invalid argv")
        env = os.environ.copy()
        env.update(
            {
                "GIT_ALLOW_PROTOCOL": "",
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_SSH_COMMAND": "false",
                "EMBED_ENABLED": "0",
                "PYTHONPATH": str(cwd),
            }
        )
        ended_because = None
        if idle or memory_gb:
            stdout, stderr, returncode, ended_because = self._run_watched(argv, cwd, env, timeout, idle or 0, memory_gb or 0)
            timed_out = bool(ended_because) and "memory" not in ended_because
            if returncode == -9 and not ended_because:
                ended_because = "the system killed this run (signal 9), almost always for using too much memory. It was NOT the time limit"
        else:
            try:
                completed = subprocess.run(
                    argv,
                    cwd=cwd,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=timeout,
                    check=False,
                    shell=False,
                )
                timed_out = False
                stdout = completed.stdout
                stderr = completed.stderr
                returncode = completed.returncode
            except subprocess.TimeoutExpired as exc:
                timed_out = True
                stdout = exc.stdout or b""
                stderr = exc.stderr or b""
                returncode = None
        # Clip head+tail (not head-only) because `unittest -v` writes verdicts to stderr tail;
        # clipping only the head would drop the OK/FAILED result. Shared impl via stream_clip.
        stdout, stdout_truncated = stream_clip.clip_bytes(stdout, self.max_output_bytes)
        stderr, stderr_truncated = stream_clip.clip_bytes(stderr, self.max_output_bytes)
        out = {
            "argv": argv,
            "returncode": returncode,
            "timed_out": timed_out,
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace"),
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
        }
        if ended_because:
            out["ended_because"] = ended_because
        return out
