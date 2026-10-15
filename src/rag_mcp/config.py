"""Configuration.

All settings come from environment variables (prefix RAG_) or from a `.env`
file in the project root. The project root is derived from this file's
location, so the server works no matter which directory it is launched from
(Claude Code launches MCP servers with its own cwd).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RAG_",
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Where documents live. Can point at a synced folder (Dropbox, OneDrive,
    # Syncthing) or a separately cloned private repo.
    docs_dir: Path = Field(default=PROJECT_ROOT / "data" / "docs")
    # Machine-local index. Never commit it; it is rebuilt with `rag-mcp ingest`.
    index_dir: Path = Field(default=PROJECT_ROOT / "data" / "index")
    collection: str = "docs"

    # Vector store: embedded Qdrant (no server needed) or a Qdrant server URL.
    # With Docker Desktop: `docker compose up -d` and set RAG_QDRANT_URL=http://localhost:6333
    qdrant_url: str | None = None
    qdrant_api_key: str | None = None

    # Embeddings
    embed_provider: Literal["ollama", "openai", "hash"] = "ollama"
    embed_model: str = "nomic-embed-text"
    ollama_url: str = "http://localhost:11434"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str | None = None
    embed_batch_size: int = 32

    # Chunking (characters; roughly 4 chars per token for English, ~2-3 for Russian)
    chunk_size: int = 1800
    chunk_overlap: int = 200

    # Multi-project layout: docs_dir/<project>/... where <project> is the name of the
    # project folder on disk. `search` auto-detects the current project from the MCP
    # client's workspace roots and scopes results to it plus these shared folders.
    shared_projects: list[str] = Field(default=["common"])
    # Force a project (skips auto-detection). Mostly for tests and scripts.
    project: str | None = None

    # Retrieval
    top_k: int = 6
    candidate_multiplier: int = 4  # how many candidates each retriever returns before fusion
    rrf_k: int = 60

    def resolve(self) -> "Settings":
        """Make relative paths absolute relative to the project root."""
        for name in ("docs_dir", "index_dir"):
            p: Path = getattr(self, name)
            if not p.is_absolute():
                setattr(self, name, (PROJECT_ROOT / p).resolve())
        return self


def load_settings() -> Settings:
    return Settings().resolve()
