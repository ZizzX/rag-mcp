"""Structure-aware chunking.

Markdown is split on headings first so a chunk never straddles two sections;
then any oversized section is split into overlapping windows at paragraph or
sentence boundaries. PDFs are chunked per page the same way. Every chunk
carries the heading path and page number as metadata for citations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .loaders import Document

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    source: str
    title: str
    text: str
    heading: str = ""  # "Section > Subsection"
    project: str = ""  # first path component of `source` ("" for files in the docs root)
    page: int | None = None
    index: int = 0
    metadata: dict = field(default_factory=dict)


def _split_sections(text: str) -> list[tuple[str, str]]:
    """Return (heading_path, body) pairs for a Markdown document."""
    matches = list(HEADING_RE.finditer(text))
    if not matches:
        return [("", text)]
    sections: list[tuple[str, str]] = []
    preamble = text[: matches[0].start()].strip()
    if preamble:
        sections.append(("", preamble))
    stack: list[tuple[int, str]] = []
    for i, m in enumerate(matches):
        level, name = len(m.group(1)), m.group(2).strip()
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, name))
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end() : end].strip()
        if not body:
            continue  # heading with no text of its own; it still appears in children's heading path
        heading = " > ".join(n for _, n in stack)
        # Keep the heading inside the chunk text: it helps both BM25 and embeddings.
        sections.append((heading, f"{name}\n\n{body}"))
    return sections


def _windows(text: str, size: int, overlap: int) -> list[str]:
    text = text.strip()
    if len(text) <= size:
        return [text] if text else []
    # Prefer breaking at paragraph, then sentence, then whitespace boundaries.
    out: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            cut = _best_break(text, start + size // 2, end)
            end = cut if cut > start else end
        piece = text[start:end].strip()
        if piece:
            out.append(piece)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return out


def _best_break(text: str, lo: int, hi: int) -> int:
    for pattern in ("\n\n", "\n", ". ", "! ", "? ", " "):
        idx = text.rfind(pattern, lo, hi)
        if idx != -1:
            return idx + len(pattern)
    return hi


def project_of(source: str) -> str:
    """'shop-backend/specs/api.pdf' -> 'shop-backend'; 'notes.md' -> ''."""
    return source.split("/", 1)[0] if "/" in source else ""


def chunk_document(doc: Document, chunk_size: int, overlap: int) -> list[Chunk]:
    chunks: list[Chunk] = []
    counter = 0
    for page in doc.pages:
        sections = _split_sections(page.text) if page.page_number is None else [("", page.text)]
        for heading, body in sections:
            for piece in _windows(body, chunk_size, overlap):
                chunks.append(
                    Chunk(
                        chunk_id=f"{doc.doc_id}:{counter}",
                        doc_id=doc.doc_id,
                        source=doc.source,
                        title=doc.title,
                        text=piece,
                        heading=heading,
                        project=project_of(doc.source),
                        page=page.page_number,
                        index=counter,
                    )
                )
                counter += 1
    return chunks
