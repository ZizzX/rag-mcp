from rag_mcp.chunking import chunk_document
from rag_mcp.loaders import Document, Page


def test_markdown_split_on_headings_keeps_heading_path():
    md = "# Title\n\nintro\n\n## A\n\ntext a\n\n### A1\n\ntext a1\n\n## B\n\ntext b"
    doc = Document(doc_id="d", source="x.md", title="Title", pages=[Page(md)])
    chunks = chunk_document(doc, chunk_size=1000, overlap=0)
    headings = [c.heading for c in chunks]
    assert headings == ["Title", "Title > A", "Title > A > A1", "Title > B"]
    assert chunks[2].text.startswith("A1")
    assert [c.chunk_id for c in chunks] == ["d:0", "d:1", "d:2", "d:3"]


def test_long_section_is_windowed_with_overlap():
    body = " ".join(f"word{i}" for i in range(400))
    doc = Document(doc_id="d", source="x.md", title="t", pages=[Page("# H\n\n" + body)])
    chunks = chunk_document(doc, chunk_size=500, overlap=100)
    assert len(chunks) > 3
    assert all(len(c.text) <= 500 for c in chunks)
    # consecutive windows share text
    assert chunks[1].text.split()[0] in chunks[0].text


def test_pdf_pages_carry_page_numbers():
    doc = Document(doc_id="d", source="x.pdf", title="t", pages=[Page("one", 1), Page("two", 2)])
    chunks = chunk_document(doc, chunk_size=100, overlap=0)
    assert [c.page for c in chunks] == [1, 2]
