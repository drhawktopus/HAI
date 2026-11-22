#!/bin/sh
set -eu

STACK="${HEAD_STACK_DIR:?HEAD_STACK_DIR is not set (the compose stack's folder on this host)}"
export HEAD_DOCS_MOUNT="${HEAD_DOCS_MOUNT:?HEAD_DOCS_MOUNT is not set (the host folder the stack binds at /docs)}"
ORCH="$STACK/orchestrator"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="$ORCH/backups/pre-coding-runner-$STAMP"

sudo mkdir -p "$BACKUP"
sudo cp \
  "$ORCH/app.py" \
  "$ORCH/config.py" \
  "$ORCH/ollama_client.py" \
  "$ORCH/tools.py" \
  "$STACK/docker-compose.override.yml" \
  "$BACKUP/"

sudo mkdir -p \
  "$ORCH/coding-runner" \
  "$STACK/coding-workspaces/sample" \
  "$STACK/coding-state" \
  "$STACK/secrets"

if [ ! -s "$STACK/secrets/coding_runner_token" ]; then
  umask 077
  openssl rand -hex 32 | sudo tee "$STACK/secrets/coding_runner_token" >/dev/null
fi
sudo chmod 600 "$STACK/secrets/coding_runner_token"

sudo tar -xzf /tmp/home-ai-coding-runner.tar.gz -C "$ORCH/coding-runner"
sudo tar -xzf /tmp/home-ai-sample-workspace.tar.gz -C "$STACK/coding-workspaces/sample"
sudo cp /tmp/home-ai-orchestrator-integration/*.py "$ORCH/"
sudo python3 /tmp/home-ai-deploy/update_compose.py

cd "$STACK"
sudo docker compose config --quiet
sudo docker compose build coding-runner orchestrator
sudo docker compose up -d coding-runner orchestrator
sudo docker compose ps coding-runner orchestrator

echo "BACKUP=$BACKUP"
