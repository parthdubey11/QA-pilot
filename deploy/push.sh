#!/usr/bin/env bash
# Upload this project to the server and (re)start it. Run from Git Bash / any shell with ssh and tar:
#   bash deploy/push.sh ubuntu@<server-ip> [path/to/ssh-key]
# First run: also copies the LLM key lines (GROQ_*/GEMINI_*/LLM_PROVIDER) from your local .env into the server's
# .env if they aren't there yet. Nothing else from your local .env is uploaded, and no secret is printed.
set -euo pipefail
cd "$(dirname "$0")/.."

HOST="${1:?usage: bash deploy/push.sh ubuntu@<server-ip> [ssh-key]}"
SSH_OPTS=(-o StrictHostKeyChecking=accept-new)
if [ -n "${2:-}" ]; then SSH_OPTS+=(-i "$2"); fi
REMOTE_DIR="qa-pilot"

echo "==> Uploading code to $HOST:~/$REMOTE_DIR"
tar czf - \
  --exclude=.git --exclude=.claude --exclude=node_modules --exclude=.venv --exclude=dist --exclude=__pycache__ \
  --exclude=.pytest_cache --exclude=storage --exclude=.env --exclude='*.pyc' --exclude=eval/results \
  . | ssh "${SSH_OPTS[@]}" "$HOST" "mkdir -p ~/$REMOTE_DIR && tar xzf - -C ~/$REMOTE_DIR"

if [ -f .env ]; then
  # Only the LLM settings, added to the server's .env when missing (values are never echoed).
  grep -E '^(LLM_PROVIDER|LLM_FALLBACKS|GROQ_API_KEY|GROQ_MODEL|GEMINI_API_KEY|GEMINI_MODEL)=' .env | \
    ssh "${SSH_OPTS[@]}" "$HOST" "cd ~/$REMOTE_DIR && touch .env && chmod 600 .env && \
      while IFS= read -r line; do key=\${line%%=*}; grep -q \"^\$key=\" .env || echo \"\$line\" >> .env; done"
fi

echo "==> Running setup on the server"
ssh -t "${SSH_OPTS[@]}" "$HOST" "cd ~/$REMOTE_DIR && bash deploy/setup.sh"
