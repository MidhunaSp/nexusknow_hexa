"""
Document Ingestion Pipeline
- Supports PDF, DOCX, PPTX, TXT, MD
- Extracts text via `unstructured`
- Splits into overlapping chunks with metadata preserved (source file, page number)
"""
import os
import uuid
from unstructured.partition.auto import partition
from backend.config import settings


def extract_elements(file_path: str):
    """Extract structured elements (paragraphs, titles, tables) from a document."""
    elements = partition(filename=file_path)
    return elements


def chunk_text(text: str, chunk_size: int = None, overlap: int = None) -> list[str]:
    """
    Simple recursive-style chunking: split on paragraph boundaries first,
    then fall back to fixed-size windows with overlap for long paragraphs.
    """
    chunk_size = chunk_size or settings.CHUNK_SIZE
    overlap = overlap or settings.CHUNK_OVERLAP

    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks = []
    current = ""

    for para in paragraphs:
        if len(current) + len(para) <= chunk_size:
            current += ("\n\n" if current else "") + para
        else:
            if current:
                chunks.append(current)
            # if a single paragraph is itself too long, window it
            if len(para) > chunk_size:
                start = 0
                while start < len(para):
                    chunks.append(para[start:start + chunk_size])
                    start += chunk_size - overlap
                current = ""
            else:
                current = para

    if current:
        chunks.append(current)

    # add overlap between adjacent chunks for better context continuity
    overlapped = []
    for i, c in enumerate(chunks):
        if i == 0:
            overlapped.append(c)
        else:
            prev_tail = chunks[i - 1][-overlap:] if overlap else ""
            overlapped.append(prev_tail + "\n\n" + c)
    return overlapped


def process_document(file_path: str, filename: str) -> dict:
    """
    Full ingestion for one file:
    1. Extract text + page metadata
    2. Chunk it
    3. Return chunks with metadata, ready for embedding
    """
    doc_id = str(uuid.uuid4())
    elements = extract_elements(file_path)

    # Group elements by page (falls back to single page if no page metadata)
    full_text_by_page = {}
    for el in elements:
        page = getattr(el.metadata, "page_number", None) or 1
        full_text_by_page.setdefault(page, []).append(str(el))

    all_chunks = []
    for page, texts in full_text_by_page.items():
        page_text = "\n\n".join(texts)
        page_chunks = chunk_text(page_text)
        for i, chunk in enumerate(page_chunks):
            all_chunks.append({
                "chunk_id": f"{doc_id}_p{page}_c{i}",
                "doc_id": doc_id,
                "filename": filename,
                "page": page,
                "text": chunk,
            })

    return {
        "doc_id": doc_id,
        "filename": filename,
        "num_chunks": len(all_chunks),
        "chunks": all_chunks,
    }
