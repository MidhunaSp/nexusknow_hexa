# Nexus Know

*Every Answer, Sourced.*

A Retrieval-Augmented Generation platform for enterprise document Q&A, built to satisfy:

- ✅ **Document ingestion** — PDF, DOCX, PPTX, TXT, MD via `unstructured`
- ✅ **Semantic search** — local embeddings (`sentence-transformers`) + ChromaDB vector store
- ✅ **RAG implementation** — retrieval + Claude (Anthropic API) generation
- ✅ **Citation-based answers** — every claim in the answer is tied to `[1]`, `[2]`... source excerpts shown in the UI
- ✅ **Knowledge governance** — role-based access control (RBAC) on document tags + full query audit log

## Architecture

```
Upload → Ingestion (chunking) → Embedding → ChromaDB
                                                  │
User query → Role check (RBAC) → Semantic search ─┘
                                       │
                              Claude (grounded prompt)
                                       │
                          Cited answer + audit log entry
```

## Setup

1. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure your API key**
   ```bash
   cp .env.example .env
   # edit .env and add your ANTHROPIC_API_KEY
   ```

3. **Run the backend (FastAPI)**
   ```bash
   uvicorn backend.main:app --reload --port 8000
   ```

4. **Run the frontend (Streamlit)** — in a second terminal
   ```bash
   streamlit run frontend/app.py
   ```

5. Open the Streamlit URL (usually `http://localhost:8501`), select a role in the sidebar,
   upload a document under the **Upload** tab (tag it, e.g. `hr` or `finance`), then ask
   questions in the **Chat** tab.

## Demoing governance

Try this to show off the RBAC layer:
1. Upload a doc tagged `finance`.
2. Switch role to `employee` (sidebar) and ask about it → the assistant says it has no
   authorized/relevant documents.
3. Switch role to `finance` or `admin` and ask again → it answers with citations.
4. Check the **Governance / Audit** tab to see every query logged, including blocked ones.

## Project structure

```
enterprise-rag/
├── backend/
│   ├── main.py          # FastAPI app & endpoints
│   ├── config.py         # settings from .env
│   ├── ingestion.py       # document parsing + chunking
│   ├── vector_store.py     # embeddings + ChromaDB semantic search
│   ├── rag_engine.py       # retrieval -> Claude prompt -> cited answer
│   └── governance.py       # RBAC policies + audit logging (SQLite)
├── frontend/
│   └── app.py            # Streamlit UI (Chat / Upload / Governance tabs)
├── data/
│   ├── uploads/           # raw uploaded files
│   └── chroma_db/          # persistent vector store
├── requirements.txt
└── .env.example
```

## Next steps / extensions

- Swap SQLite audit log for Postgres in a real deployment
- Add hybrid search (BM25 + vector) and a re-ranker for better retrieval precision
- Add an evaluation harness (e.g. RAGAS) to score faithfulness/relevance over time
- Add real auth (SSO/OAuth) instead of the role dropdown
- Add document versioning / staleness detection (flag docs older than N months)
