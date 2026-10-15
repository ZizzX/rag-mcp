#!/usr/bin/env bash
# rag-mcp installer for macOS and Linux.
# Idempotent: safe to run again after `git pull`.
#
#   ./install.sh                 # install, create .env, pull the Ollama model, register in Claude Code
#   ./install.sh --no-claude     # skip Claude Code registration
#   ./install.sh --no-ollama     # skip pulling the embedding model
set -euo pipefail

cd "$(dirname "$0")"
ROOT="$(pwd)"
NO_CLAUDE=0; NO_OLLAMA=0
for a in "$@"; do
  case "$a" in
    --no-claude) NO_CLAUDE=1 ;;
    --no-ollama) NO_OLLAMA=1 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }

# 1. uv (manages Python itself, so no system Python is required)
if ! command -v uv >/dev/null 2>&1; then
  say "Installing uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
say "uv $(uv --version | awk '{print $2}')"

# 2. Python + dependencies (pinned by uv.lock)
say "Installing Python $(cat .python-version) and dependencies"
uv sync --quiet

# 3. Local config
if [ ! -f .env ]; then
  cp .env.example .env
  say "Created .env from .env.example (edit RAG_DOCS_DIR to point at your documents)"
fi
mkdir -p data/docs data/index

# 4. Embedding model via Ollama
EMBED_MODEL="$(grep -E '^RAG_EMBED_MODEL=' .env | cut -d= -f2- || true)"; EMBED_MODEL="${EMBED_MODEL:-nomic-embed-text}"
PROVIDER="$(grep -E '^RAG_EMBED_PROVIDER=' .env | cut -d= -f2- || true)"; PROVIDER="${PROVIDER:-ollama}"
if [ "$NO_OLLAMA" = 0 ] && [ "$PROVIDER" = ollama ]; then
  if command -v ollama >/dev/null 2>&1; then
    say "Pulling Ollama model $EMBED_MODEL"
    ollama pull "$EMBED_MODEL" || echo "WARN: could not pull $EMBED_MODEL; is the Ollama app running?" >&2
  else
    echo "WARN: ollama not found. Install from https://ollama.com and run: ollama pull $EMBED_MODEL" >&2
  fi
fi

# 5. Register the MCP server in Claude Code (user scope: available in every project)
if [ "$NO_CLAUDE" = 0 ] && command -v claude >/dev/null 2>&1; then
  say "Registering MCP server 'rag-mcp' in Claude Code (user scope)"
  claude mcp remove -s user rag-mcp >/dev/null 2>&1 || true
  claude mcp add -s user rag-mcp -- uv --directory "$ROOT" run rag-mcp serve
fi

cat <<MSG

Done. Next steps:
  1. Put Markdown/PDF files into data/docs (or set RAG_DOCS_DIR in .env)
  2. uv run rag-mcp ingest
  3. uv run rag-mcp search "your question"
  4. In Claude Code: /mcp  (rag-mcp should be listed), then just ask questions about your docs.

Manual MCP config for other clients (Claude Desktop, Cursor):
  command: uv   args: ["--directory", "$ROOT", "run", "rag-mcp", "serve"]
MSG
