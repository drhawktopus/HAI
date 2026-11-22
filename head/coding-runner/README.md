# HAI's coding runner

The sandbox HAI writes code in. HAI's coding jobs open a registered workspace here, read and edit its files, run its
allow-listed tests and keep local Git history, and nothing else.

## What it can do

- List and open registered workspaces
- Bounded file listing and reading, and ripgrep search
- Git status, a complete diff (including safe untracked text files), and log
- Exact text replacement guarded by the file's SHA-256, and new UTF-8 files
- Allow-listed tests
- Durable task records and plans; pause, cancel, resume, and a list of unfinished tasks
- Local Git checkpoints with a guarded restore, and isolated task worktrees to accept or discard
- An audit log (JSONL)

It cannot run arbitrary commands, install packages, push Git history, deploy, use SSH, read credentials, or read or
write outside a registered workspace. A checkpoint commit is a local restore point inside the sandbox only.

## Local-only Git

- A registered Git workspace must have no remotes. The runner refuses to start with a repository that has `origin`
  or any other remote.
- Git transport is disabled for every process the runner starts, allow-listed tests included.
- Local commits, branches, worktrees, merges, checkpoints and restores all work.
- Remote hosting (GitHub, GitLab and the like) is out of scope by design; adding it would be a deliberate policy and
  code change, not a setting.

## Security

- The runner is internal to the stack's Docker network; its port (8100) is not published on the host.
- The head and the runner share one token, a file mounted read-only into both.
- Only registered workspaces are writable. The runner rejects traversal, absolute and drive paths, colon paths,
  symlink escapes, protected paths, stale hashes, binary or oversized files, and unknown test profiles.
- The complete diff skips protected, binary and oversized untracked files.

## Where things are, in the full-stack example

`examples/full-stack/docker-compose.yml` runs it as the `coding-runner` service, beside the head:

| What | Where |
|---|---|
| The runner's source | `head/coding-runner/` (built with the head's folder as the context) |
| Workspaces | `examples/full-stack/coding-workspaces/` (mounted at `/app/workspaces`); copy `head/coding-runner/workspaces/sample` in to try it |
| Its state and audit log | `examples/full-stack/coding-state/` |
| Folders a workspace may be registered in, once you approve it | `examples/full-stack/shares/` |
| The shared token | `examples/full-stack/state/secrets/coding_runner_token` (the stack's setup makes it) |

The head finds it through `CODING_RUNNER_URL` and `CODING_RUNNER_TOKEN_FILE` in `sidecars.env`.

## A second look at each task

A task can get one review pass: a fresh-context run of the lead model that receives the task's evidence (the diff and
the test results) and has no coding tools and no write access. Four seeded benchmark cases pin what the reviewer must
catch.
