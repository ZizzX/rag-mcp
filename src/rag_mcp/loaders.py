"""Load Markdown and PDF files into Documents."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

SUPPORTED_SUFFIXES = {".md", ".markdown", ".txt", ".pdf"}


@dataclass
class Page:
    text: str
    page_number: int | None = None  # 1-based for PDFs, None for Markdown


@dataclass
class Document:
    doc_id: str
    source: str  # path relative to docs_dir, with forward slashes
    title: str
    pages: list[Page] = field(default_factory=list)
    content_hash: str = ""

    @property
    def text(self) -> str:
        return "\n\n".join(p.text for p in self.pages)


def doc_id_for(source: str) -> str:
    return hashlib.sha1(source.encode("utf-8")).hexdigest()[:16]


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def iter_files(docs_dir: Path) -> Iterator[Path]:
    for p in sorted(docs_dir.rglob("*")):
        if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES and not _hidden(p, docs_dir):
            yield p


def _hidden(p: Path, root: Path) -> bool:
    return any(part.startswith(".") for part in p.relative_to(root).parts)


def load_markdown(path: Path) -> list[Page]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return [Page(text=text)]


def load_pdf(path: Path) -> list[Page]:
    import pymupdf  # imported lazily: it is the heaviest dependency

    pages: list[Page] = []
    with pymupdf.open(path) as doc:
        for i, page in enumerate(doc, start=1):
            text = page.get_text("text").strip()
            if text:
                pages.append(Page(text=text, page_number=i))
    return pages


def markdown_title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip() or fallback
        if line:
            break
    return fallback


def load_document(path: Path, source: str) -> Document:
    """`source` is the document's name in the index: its path under docs_dir, or `<project>/<path>`."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        pages = load_pdf(path)
        title = path.stem
    else:
        pages = load_markdown(path)
        title = markdown_title(pages[0].text if pages else "", path.stem)
    return Document(
        doc_id=doc_id_for(source),
        source=source,
        title=title,
        pages=pages,
        content_hash=file_hash(path),
    )
