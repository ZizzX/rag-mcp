"""Ingest and query orchestration."""

from __future__ import annotations

import atexit
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .chunking import chunk_document
from .config import Settings, load_settings
from .embeddings import make_embedder
from .loaders import doc_id_for, file_hash, iter_files, load_document
from .projects import Project, ProjectError, RegistryError, list_files, load_projects, resolve_project, save_projects
from .store import HybridStore

log = logging.getLogger("rag_mcp")

# Bump when the chunk payload changes in a way that needs a full re-embed.
SCHEMA_VERSION = 2


@dataclass
class IngestReport:
    added: int = 0
    updated: int = 0
    removed: int = 0
    unchanged: int = 0
    chunks: int = 0
    errors: list[str] = None  # type: ignore[assignment]

    def __post_init__(self):
        self.errors = self.errors or []


@dataclass
class SourceScan:
    """Every document the index should hold right now, and what could not be looked at."""

    files: list[tuple[Path, str]] = field(default_factory=list)  # (file, source)
    missing: list[str] = field(default_factory=list)  # connected projects whose folder cannot be read
    warnings: list[str] = field(default_factory=list)
    projects: dict[str, Project] = field(default_factory=dict)
    docs_dir_found: bool = True

    def owner(self, source: str) -> str | None:
        """The connected project a source belongs to; None for documents of docs_dir."""
        top = source.split("/", 1)[0] if "/" in source else ""
        return top if top in self.projects else None

    def is_kept(self, entry: dict) -> bool:
        """A manifest entry stays although no file was seen for it: its folder is unreachable.
        Entries carry `project` when they come from a connected project (see _ingest)."""
        owner = entry.get("project")
        if owner is not None:
            return owner in self.missing
        return not self.docs_dir_found  # unmounted drive, wrong RAG_DOCS_DIR


class Pipeline:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or load_settings()
        self.embedder = make_embedder(self.settings)
        self.store = self._open_store()
        atexit.register(self.store.close)

    def _open_store(self) -> HybridStore:
        s = self.settings
        return HybridStore(
            index_dir=s.index_dir,
            collection=s.collection,
            embedder=self.embedder,
            qdrant_url=s.qdrant_url,
            qdrant_api_key=s.qdrant_api_key,
            rrf_k=s.rrf_k,
        )

    def scan(self) -> SourceScan:
        """Documents of docs_dir plus every connected project. Raises RegistryError."""
        s = self.settings
        out = SourceScan(projects=load_projects(s.index_dir), docs_dir_found=s.docs_dir.is_dir())
        if out.docs_dir_found:
            shadowed: set[str] = set()
            for p in iter_files(s.docs_dir):
                source = p.relative_to(s.docs_dir).as_posix()
                if out.owner(source):  # data/docs/<name>/ next to a connected project <name>
                    shadowed.add(out.owner(source))
                    continue
                out.files.append((p, source))
            out.warnings += [
                f"{s.docs_dir / name} is shadowed by the connected project {name}; rename one of them"
                for name in sorted(shadowed)
            ]
        for project in out.projects.values():
            try:
                os.scandir(project.path).close()  # gone, not a folder, or not readable (macOS privacy rules)
            except OSError:
                out.missing.append(project.name)
                continue
            files, warning = list_files(project.path, skip=s.docs_dir)
            out.files += [(f, f"{project.name}/{f.relative_to(project.path).as_posix()}") for f in files]
            if warning:
                out.warnings.append(f"project {project.name}: {warning}")
        return out

    def needs_ingest(self) -> bool:
        """True when a document was added, changed or removed since the last commit."""
        try:
            scan = self.scan()
        except RegistryError:
            return False  # ingest would refuse anyway; searching what is indexed still works
        docs = {k: v for k, v in self.store.manifest().items() if k != "_meta" and not scan.is_kept(v)}
        if len(scan.files) != len(docs):
            return True
        # Compare each file with what the last ingest saw (see _stat), not with
        # the manifest's own mtime: a file copied with `cp -p` or unpacked keeps an old mtime.
        for path, source in scan.files:
            entry = docs.get(doc_id_for(source))
            if entry is None or entry.get("stat") != _stat(path):
                return True
        return False

    def index_problem(self) -> str | None:
        """Why searching the index would give nothing useful, in words for the user; None when it is fine."""
        docs = {k: v for k, v in self.store.manifest().items() if k != "_meta"}
        if not docs or not self.store.count():
            return (
                f"The index is empty. Put Markdown/PDF files into {self.settings.docs_dir} "
                "and call the `reindex` tool (or run `rag-mcp ingest`)."
            )
        built_with = {m.get("embedder") for m in docs.values()} - {None, self.embedder.name}
        if built_with:
            return (
                f"The index was built with {', '.join(sorted(built_with))}, but the server now uses "
                f"{self.embedder.name}. Run `rag-mcp ingest --rebuild`."
            )
        return None

    def ingest(self, rebuild: bool = False) -> IngestReport:
        """Index new and changed files. Raises IndexBusy if another process is writing."""
        s = self.settings
        if not s.docs_dir.exists() and not load_projects(s.index_dir) and not self._indexed_documents():
            raise FileNotFoundError(f"Documents directory does not exist: {s.docs_dir}")
        # Write through a separate store, so searches on self.store never see a half-applied
        # update; self.store picks the commit up from disk on its next read. The in-process
        # Qdrant used by tests cannot be shared that way, so it writes in place.
        writer = self.store if self.store.in_memory else self._open_store()
        try:
            with writer.write_lock():
                writer.reload()  # another process may have committed since we opened it
                return self._ingest(writer, rebuild)
        finally:
            if writer is not self.store:
                writer.close()

    def _ingest(self, store: HybridStore, rebuild: bool) -> IngestReport:
        s = self.settings
        report = IngestReport()
        scan = self.scan()  # first: a broken projects.json must stop us before anything changes
        if rebuild:
            before = [m for k, m in store.manifest().items() if k != "_meta"]
            lost = sorted({m["project"] for m in before if m.get("project") in scan.missing})
            if not scan.docs_dir_found and any("project" not in m for m in before):
                lost.append(str(s.docs_dir))
            if lost:
                raise ProjectError(
                    f"--rebuild would drop the documents of {', '.join(lost)}: the folder cannot be read. "
                    "Bring it back, or `rag-mcp remove` the project first"
                )
            store.reset()
        manifest = store.manifest()
        docs = {k: v for k, v in manifest.items() if k != "_meta"}
        if docs and any(m.get("embedder") not in (None, self.embedder.name) for m in docs.values()):
            raise RuntimeError(
                "Index was built with a different embedding model. Run `rag-mcp ingest --rebuild`."
            )
        if docs and manifest.get("_meta", {}).get("schema") != SCHEMA_VERSION:
            raise RuntimeError("Index format changed in this version of rag-mcp. Run `rag-mcp ingest --rebuild`.")
        manifest["_meta"] = {"schema": SCHEMA_VERSION}

        seen: set[str] = {d for d, m in docs.items() if scan.is_kept(m)}
        for name in scan.missing:
            report.errors.append(
                f"project {name}: folder not found or not readable ({scan.projects[name].path}); its documents "
                f"stay in the index. Run `rag-mcp remove {name}`, or `rag-mcp add` it again from its new place"
            )
        if not scan.docs_dir_found and any("project" not in m for m in docs.values()):
            report.errors.append(f"{s.docs_dir} not found; its documents stay in the index")
        report.errors += scan.warnings
        for path, source in scan.files:
            doc_id = doc_id_for(source)
            seen.add(doc_id)
            prev = manifest.get(doc_id)
            try:
                stat = _stat(path)
                content_hash = file_hash(path)
                owner = scan.owner(source)
                if prev and prev.get("content_hash") == content_hash:
                    prev["stat"] = stat  # touched or copied back: same content, remember the new mtime
                    if owner:
                        prev["project"] = owner
                    report.unchanged += 1
                    continue
                doc = load_document(path, source)
                chunks = chunk_document(doc, s.chunk_size, s.chunk_overlap)
                if prev:
                    store.delete_document(doc_id)
                    report.updated += 1
                else:
                    report.added += 1
                store.upsert_chunks(chunks)
                manifest[doc_id] = {
                    "source": source,
                    "content_hash": content_hash,
                    "chunks": len(chunks),
                    "embedder": self.embedder.name,
                    "stat": stat,
                    **({"project": owner} if owner else {}),
                }
                report.chunks += len(chunks)
                log.info("indexed %s (%d chunks)", source, len(chunks))
            except Exception as e:  # keep going; report at the end
                report.errors.append(f"{source}: {e}")
                log.warning("failed %s: %s", source, e)

        for doc_id in [d for d in manifest if d != "_meta" and d not in seen]:
            store.delete_document(doc_id)
            del manifest[doc_id]
            report.removed += 1

        store.commit(manifest)
        return report

    def search(
        self,
        query: str,
        top_k: int | None = None,
        source_prefixes: list[str] | None = None,
        dense_errors: list[Exception] | None = None,
    ) -> list[dict]:
        return self.store.search(
            query,
            top_k=top_k or self.settings.top_k,
            candidate_multiplier=self.settings.candidate_multiplier,
            source_prefixes=source_prefixes,
            dense_errors=dense_errors,
        )

    # ---------- multi-project scoping ----------
    def detect_project(self, workspace_paths: list[str]) -> str | None:
        """Map the client's workspace folder(s) to an indexed project.

        Walks up from each path and returns the first ancestor whose folder name is an
        indexed top-level folder, so a subfolder of a project still resolves to it.
        RAG_PROJECT overrides detection.
        """
        if self.settings.project:
            return self.settings.project
        indexed = set(self.store.projects())
        if not indexed:
            return None
        for raw in workspace_paths:
            path = Path(raw)
            for candidate in (path, *path.parents):
                if candidate.name in indexed:
                    return candidate.name
        return None

    def scope_for(self, project: str | None) -> list[str] | None:
        """Prefixes to search for a project: the project itself plus shared folders that exist."""
        if not project:
            return None
        indexed = set(self.store.projects())
        shared = [s for s in self.settings.shared_projects if s in indexed and s != project]
        return [f"{project}/", *(f"{s}/" for s in shared)]

    def document(self, doc_id: str) -> dict | None:
        chunks = self.store.chunks_for_document(doc_id)
        if not chunks:
            return None
        return {
            "doc_id": doc_id,
            "source": chunks[0]["source"],
            "title": chunks[0]["title"],
            "chunks": len(chunks),
            "text": "\n\n".join(c["text"] for c in chunks),
        }

    def _indexed_documents(self) -> bool:
        return any(k != "_meta" for k in self.store.manifest())

    # ---------- connected projects ----------
    def add_project(
        self, path: str | Path | None, name: str | None = None, *, cwd: Path | None = None, force: bool = False
    ) -> tuple[Project, int]:
        """Register a project folder (see projects.resolve_project); the caller runs ingest() after.
        Raises ProjectError, RegistryError or IndexBusy."""
        s = self.settings
        with self.store.write_lock():
            existing = load_projects(s.index_dir)
            project, files = resolve_project(
                path, name, cwd=cwd or Path.cwd(), docs_dir=s.docs_dir, existing=existing, force=force
            )
            if project.name in existing:
                project.added = existing[project.name].added
            save_projects(s.index_dir, {**existing, project.name: project})
        return project, len(files)

    def remove_project(self, name: str) -> Project:
        """Unregister a project; the next ingest() drops its documents."""
        s = self.settings
        with self.store.write_lock():
            existing = load_projects(s.index_dir)
            if name not in existing:
                raise ProjectError(f"There is no project {name!r}; see `rag-mcp projects`")
            project = existing.pop(name)
            save_projects(s.index_dir, existing)
        return project

    def projects_info(self) -> list[dict]:
        """Connected projects and project folders of docs_dir, with what the index holds for each."""
        s = self.settings
        manifest = [m.get("source", "") for k, m in self.store.manifest().items() if k != "_meta"]
        per_source = self.store.sources()

        def row(name: str, kind: str, path: Path, exists: bool) -> dict:
            prefix = f"{name}/"
            return {
                "name": name, "kind": kind, "path": str(path), "exists": exists,
                "documents": sum(1 for src in manifest if src.startswith(prefix)),
                "chunks": sum(v["chunks"] for src, v in per_source.items() if src.startswith(prefix)),
            }

        rows = [row(p.name, "connected", p.path, p.path.is_dir()) for p in load_projects(s.index_dir).values()]
        if s.docs_dir.is_dir():
            rows += [
                row(d.name, "folder", d, True)
                for d in sorted(s.docs_dir.iterdir()) if d.is_dir() and not d.name.startswith(".")
            ]
        return rows

    def status(self) -> dict:
        s = self.settings
        try:
            connected: dict = {p.name: str(p.path) for p in load_projects(s.index_dir).values()}
        except RegistryError as e:
            connected = {"error": str(e)}
        return {
            "docs_dir": str(s.docs_dir),
            "index_dir": str(s.index_dir),
            "vector_store": s.qdrant_url or "local (vectors.npy)",
            "embedder": self.embedder.name,
            "documents": sum(1 for k in self.store.manifest() if k != "_meta"),
            "chunks": self.store.count(),
            "projects": self.store.projects(),
            "shared_projects": self.settings.shared_projects,
            "connected_projects": connected,
        }


def _stat(path: Path) -> list[int]:
    """What `needs_ingest` compares, without reading the file. Size and mtime catch edits; ctime and
    inode catch a replacement that kept both (cp -p, unpacking an archive): setting an mtime moves
    ctime, and a file moved into place is a new inode."""
    st = path.stat()
    # ponytail: on Windows st_ctime is the creation time, so an in-place overwrite there that keeps
    # size and mtime still goes unnoticed; `reindex` picks it up. Hash every file if that ever matters.
    return [st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_ino]


def configure_logging(verbose: bool = False) -> None:
    # MCP over stdio uses stdout for protocol messages: logs must go to stderr.
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        stream=sys.stderr,
        format="%(levelname)s %(name)s: %(message)s",
    )
