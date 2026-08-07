"""
Vector Store — Semantic Search Layer
- Embeds chunks with a local sentence-transformers model (free, no API key)
- Stores/retrieves via ChromaDB (persistent, local)
- Supports metadata filtering (for governance/ACL enforcement at query time)
"""
import os
# Force transformers to use PyTorch only, ignoring any (possibly broken/mismatched)
# TensorFlow installation elsewhere on the system. Must be set before the
# sentence_transformers/transformers imports below.
os.environ["USE_TF"] = "0"
os.environ["USE_TORCH"] = "1"
os.environ["TRANSFORMERS_NO_ADVISORY_WARNINGS"] = "1"

import chromadb
from sentence_transformers import SentenceTransformer
from backend.config import settings

_embedder = None
_client = None
_collection = None


def get_embedder():
    global _embedder
    if _embedder is None:
        _embedder = SentenceTransformer(settings.EMBEDDING_MODEL)
    return _embedder


def get_collection():
    global _client, _collection
    if _collection is None:
        _client = chromadb.PersistentClient(path=settings.CHROMA_DB_PATH)
        _collection = _client.get_or_create_collection(
            name=settings.COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"}
        )
    return _collection


def embed_texts(texts: list[str]) -> list[list[float]]:
    model = get_embedder()
    return model.encode(texts, normalize_embeddings=True).tolist()


def add_chunks(chunks: list[dict], tags: list[str]):
    """
    Add document chunks to the vector store.
    `tags` (e.g. ["hr", "general"]) are stored as metadata for ACL filtering later.
    """
    if not chunks:
        return

    collection = get_collection()
    texts = [c["text"] for c in chunks]
    embeddings = embed_texts(texts)

    collection.add(
        ids=[c["chunk_id"] for c in chunks],
        embeddings=embeddings,
        documents=texts,
        metadatas=[{
            "doc_id": c["doc_id"],
            "filename": c["filename"],
            "page": c["page"],
            "tags": ",".join(tags),
        } for c in chunks],
    )


def semantic_search(query: str, top_k: int = None, allowed_tags: list[str] = None) -> list[dict]:
    """
    Retrieve the most relevant chunks for a query.
    If allowed_tags is provided (and doesn't contain "*"), results are filtered
    to only chunks whose tags intersect with allowed_tags — this is how RBAC
    is enforced at retrieval time, not just in the UI.
    """
    top_k = top_k or settings.TOP_K_RESULTS
    collection = get_collection()
    query_embedding = embed_texts([query])[0]

    # Over-fetch, then filter by tags in Python (Chroma's metadata filtering
    # on comma-joined strings is limited; for production, use a proper
    # multi-value metadata field or a dedicated permissions table).
    raw_top_k = top_k * 4 if allowed_tags and "*" not in allowed_tags else top_k

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=min(raw_top_k, max(collection.count(), 1)),
    )

    hits = []
    if not results["ids"] or not results["ids"][0]:
        return hits

    for i in range(len(results["ids"][0])):
        meta = results["metadatas"][0][i]
        doc_tags = meta["tags"].split(",") if meta["tags"] else []

        if allowed_tags and "*" not in allowed_tags:
            if not any(t in allowed_tags for t in doc_tags):
                continue  # ACL: skip chunks this role can't see

        hits.append({
            "chunk_id": results["ids"][0][i],
            "text": results["documents"][0][i],
            "filename": meta["filename"],
            "page": meta["page"],
            "doc_id": meta["doc_id"],
            "distance": results["distances"][0][i],
        })

        if len(hits) >= top_k:
            break

    return hits
