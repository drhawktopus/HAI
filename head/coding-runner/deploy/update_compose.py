"""Idempotently add the internal coding-runner service to a compose override."""

from pathlib import Path
import os
import sys


STACK = os.environ.get("HEAD_STACK_DIR", "").rstrip("/")
if not STACK:
    sys.exit("HEAD_STACK_DIR is not set (the compose stack's folder on this host)")
path = Path(sys.argv[1] if len(sys.argv) > 1 else STACK + "/docker-compose.override.yml")
text = path.read_text(encoding="utf-8")
text = text.replace(
    f"      - {STACK}/coding-workspaces/sample:/app/workspaces/sample\n",
    f"      - {STACK}/coding-workspaces:/app/workspaces\n",
)

if "CODING_RUNNER_URL=" not in text:
    environment_marker = "      - HA_TOKEN="
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith(environment_marker):
            lines[index + 1:index + 1] = [
                "      - CODING_RUNNER_URL=http://coding-runner:8100",
                "      - CODING_RUNNER_TOKEN_FILE=/run/secrets/coding_runner_token",
                "      - CODING_TOOL_LOOP_MAX=20",
            ]
            break
    else:
        raise SystemExit("orchestrator environment marker was not found")

    docs_mount = os.environ.get("HEAD_DOCS_MOUNT", "")
    if not docs_mount:
        raise SystemExit("HEAD_DOCS_MOUNT is not set (the host folder the stack binds at /docs)")
    volume_marker = "      - %s:/docs" % docs_mount
    for index, line in enumerate(lines):
        if line == volume_marker:
            lines.insert(
                index + 1,
                f"      - {STACK}/secrets/coding_runner_token:/run/secrets/coding_runner_token:ro",
            )
            break
    else:
        raise SystemExit("orchestrator volume marker was not found")
    text = "\n".join(lines) + "\n"

if "\n  coding-runner:\n" not in text:
    text += f"""
  coding-runner:
    build: ./orchestrator/coding-runner
    container_name: coding-runner
    restart: unless-stopped
    networks:
      - arrnet
    environment:
      - CODING_RUNNER_STATE=/state
      - CODING_RUNNER_TOKEN_FILE=/run/secrets/coding_runner_token
    volumes:
      - {STACK}/coding-workspaces:/app/workspaces
      - {STACK}/coding-state:/state
      - {STACK}/secrets/coding_runner_token:/run/secrets/coding_runner_token:ro
"""

path.write_text(text, encoding="utf-8")
