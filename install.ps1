# rag-mcp installer for Windows (PowerShell 5.1+ or PowerShell 7).
# Idempotent: safe to run again after `git pull`.
#
#   powershell -ExecutionPolicy Bypass -File .\install.ps1
#   .\install.ps1 -NoClaude      # skip Claude Code registration
#   .\install.ps1 -NoOllama      # skip pulling the embedding model
param(
    [switch]$NoClaude,
    [switch]$NoOllama
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$Root = (Get-Location).Path

function Say($msg) { Write-Host "==> $msg" -ForegroundColor Blue }

# 1. uv
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Say "Installing uv"
    irm https://astral.sh/uv/install.ps1 | iex
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
Say "uv $((uv --version).Split(' ')[1])"

# 2. Python + dependencies
Say "Installing Python $(Get-Content .python-version) and dependencies"
uv sync --quiet

# 3. Local config
if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Say "Created .env from .env.example (edit RAG_DOCS_DIR to point at your documents)"
}
New-Item -ItemType Directory -Force -Path data\docs, data\index | Out-Null

# 4. Embedding model via Ollama
function EnvValue($name, $default) {
    $line = Select-String -Path .env -Pattern "^$name=" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($line) { return $line.Line.Substring($name.Length + 1) } else { return $default }
}
$EmbedModel = EnvValue "RAG_EMBED_MODEL" "nomic-embed-text"
$Provider   = EnvValue "RAG_EMBED_PROVIDER" "ollama"
if (-not $NoOllama -and $Provider -eq "ollama") {
    if (Get-Command ollama -ErrorAction SilentlyContinue) {
        Say "Pulling Ollama model $EmbedModel"
        try { ollama pull $EmbedModel } catch { Write-Warning "Could not pull $EmbedModel; is the Ollama app running?" }
    } else {
        Write-Warning "ollama not found. Install from https://ollama.com and run: ollama pull $EmbedModel"
    }
}

# 5. Register the MCP server in Claude Code (user scope)
if (-not $NoClaude -and (Get-Command claude -ErrorAction SilentlyContinue)) {
    Say "Registering MCP server 'rag-mcp' in Claude Code (user scope)"
    try { claude mcp remove -s user rag-mcp 2>$null | Out-Null } catch {}
    claude mcp add -s user rag-mcp -- uv --directory "$Root" run rag-mcp serve
}

Write-Host @"

Done. Next steps:
  1. Put Markdown/PDF files into data\docs (or set RAG_DOCS_DIR in .env)
  2. uv run rag-mcp ingest
  3. uv run rag-mcp search "your question"
  4. In Claude Code: /mcp  (rag-mcp should be listed), then just ask questions about your docs.

Manual MCP config for other clients (Claude Desktop, Cursor):
  command: uv   args: ["--directory", "$Root", "run", "rag-mcp", "serve"]
"@
