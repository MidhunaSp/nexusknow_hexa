"""
Nexus Know — API Layer
Endpoints:
  POST /auth/login      - username+password login, returns a session token
  POST /auth/logout      - invalidate a session token
  POST /ingest        - upload & process a document (admin session required)
  POST /query          - ask a question, get a cited answer
  GET  /documents       - list ingested documents
  GET  /audit-log        - view query audit trail
  GET  /roles           - list available roles (for the demo-mode fallback UI)
"""
import os
import shutil
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

from backend.config import settings
from backend import governance, ingestion, vector_store, rag_engine, auth

app = FastAPI(title="Nexus Know")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs(settings.UPLOAD_PATH, exist_ok=True)
os.makedirs(settings.CHROMA_DB_PATH, exist_ok=True)
governance.init_audit_db()
auth.init_auth_db()


class QueryRequest(BaseModel):
    query: str
    user_id: str = "demo_user"     # ignored if a valid session is presented
    role: str = "employee"          # ignored if a valid session is presented
    history: list[dict] = []        # recent {"role","content"} turns, for follow-up questions


class LoginRequest(BaseModel):
    username: str
    password: str


class TagUpdateRequest(BaseModel):
    tags: list[str]


def resolve_identity(authorization: Optional[str], fallback_user_id: str, fallback_role: str):
    """Shared by /query and /suggested-questions: session wins if present, else demo fallback."""
    user = auth.get_current_user(authorization)
    if user:
        return user["username"], user["role"], "authenticated"
    return fallback_user_id, fallback_role, "demo_fallback"


@app.get("/")
def root():
    return {"status": "ok", "service": "Nexus Know"}


@app.get("/roles")
def get_roles():
    return {"roles": list(governance.ROLE_POLICIES.keys())}


@app.post("/auth/login")
def login(req: LoginRequest):
    role = auth.authenticate(req.username, req.password)
    if not role:
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    session = auth.create_session(req.username, role)
    return session


@app.post("/auth/logout")
def logout(authorization: Optional[str] = Header(None)):
    if authorization and authorization.startswith("Bearer "):
        auth.delete_session(authorization[len("Bearer "):].strip())
    return {"status": "ok"}


@app.post("/ingest")
async def ingest_document(
    file: UploadFile = File(...),
    tags: str = Form("general"),
    authorization: Optional[str] = Header(None),
):
    """Upload and process a document. Requires an authenticated admin session —
    there is no demo-mode fallback for uploads, by design."""
    user = auth.get_current_user(authorization)
    if not user:
        raise HTTPException(status_code=401, detail="Login required to upload documents.")
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admin accounts can upload documents.")

    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    file_path = os.path.join(settings.UPLOAD_PATH, file.filename)

    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        result = ingestion.process_document(file_path, file.filename)
        vector_store.add_chunks(result["chunks"], tags=tag_list)

        # Non-critical: sample a bit of the document's text and ask the model for
        # example questions. If this fails for any reason, ingestion still succeeds.
        sample_text = " ".join(c["text"] for c in result["chunks"][:3])
        sample_questions = rag_engine.generate_sample_questions(sample_text) if sample_text else []

        governance.register_document(
            doc_id=result["doc_id"],
            filename=result["filename"],
            tags=tag_list,
            uploaded_by=user["username"],
            num_chunks=result["num_chunks"],
            sample_questions=sample_questions,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {str(e)}")

    return {
        "doc_id": result["doc_id"],
        "filename": result["filename"],
        "num_chunks": result["num_chunks"],
        "tags": tag_list,
        "sample_questions": sample_questions,
    }


@app.post("/suggest-tags")
async def suggest_tags_endpoint(file: UploadFile = File(...), authorization: Optional[str] = Header(None)):
    """
    Admin-only helper: reads a sample of an about-to-be-uploaded file and asks the
    model which tag(s) it likely belongs to. Does not ingest anything — purely advisory,
    the admin can still edit the tags before clicking Ingest.
    """
    user = auth.get_current_user(authorization)
    if not user or user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admin accounts can use tag suggestions.")

    tmp_path = os.path.join(settings.UPLOAD_PATH, f"_tagpreview_{file.filename}")
    with open(tmp_path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    try:
        parsed = ingestion.process_document(tmp_path, file.filename)
        sample_text = " ".join(c["text"] for c in parsed["chunks"][:3])
        known_tags = governance.get_all_tags_in_use()
        suggestion = rag_engine.suggest_tags(sample_text, known_tags=known_tags)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Tag suggestion failed: {str(e)}")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    return suggestion


@app.get("/suggested-questions")
def suggested_questions(user_id: str = "demo_user", role: str = "employee",
                         authorization: Optional[str] = Header(None)):
    """RBAC-aware example questions, pulled from whatever documents this role can see."""
    _, resolved_role, _ = resolve_identity(authorization, user_id, role)
    allowed_tags = governance.get_allowed_tags(resolved_role)
    return {"questions": governance.get_sample_questions_for_tags(allowed_tags)}


@app.post("/query")
def query(req: QueryRequest, authorization: Optional[str] = Header(None)):
    """
    Ask a question. If a valid session is presented, the role is taken from the
    account server-side — the request body's role field is ignored, which is what
    prevents a client from just claiming to be HR. Without a session, falls back to
    the client-supplied user_id/role (demo mode), clearly tagged as such in the audit log.
    """
    user_id, role, auth_mode = resolve_identity(authorization, req.user_id, req.role)

    allowed_tags = governance.get_allowed_tags(role)
    result = rag_engine.answer_query(req.query, allowed_tags=allowed_tags, history=req.history)

    governance.log_query(
        user_id=user_id,
        role=role,
        query=req.query,
        retrieved_doc_ids=result["retrieved_doc_ids"],
        answer=result["answer"],
        blocked_by_acl=(len(result["citations"]) == 0),
        search_trace=result.get("search_trace", []),
        auth_mode=auth_mode,
    )

    return {**result, "role_used": role, "auth_mode": auth_mode}


@app.get("/documents")
def get_documents():
    return {"documents": governance.list_documents()}


@app.patch("/documents/{doc_id}/tags")
def retag_document(doc_id: str, req: TagUpdateRequest, authorization: Optional[str] = Header(None)):
    """
    Admin-only: fix a document's tags after the fact (e.g. an AI tag suggestion was
    too broad) without deleting and re-uploading, which would leave orphaned chunks
    behind under a new doc_id. Updates both the vector store and the documents table.
    """
    user = auth.get_current_user(authorization)
    if not user or user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admin accounts can edit document tags.")

    updated_count = vector_store.update_tags(doc_id, req.tags)
    if updated_count == 0:
        raise HTTPException(status_code=404, detail=f"No chunks found for doc_id {doc_id}.")
    governance.update_document_tags(doc_id, req.tags)
    return {"doc_id": doc_id, "tags": req.tags, "chunks_updated": updated_count}


@app.delete("/documents/{doc_id}")
def delete_document(doc_id: str, authorization: Optional[str] = Header(None)):
    """Admin-only: fully remove a document and its chunks from the knowledge base."""
    user = auth.get_current_user(authorization)
    if not user or user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admin accounts can delete documents.")

    deleted_count = vector_store.delete_document(doc_id)
    governance.delete_document_record(doc_id)
    return {"doc_id": doc_id, "chunks_deleted": deleted_count}


@app.get("/audit-log")
def get_audit_log(limit: int = 50):
    return {"log": governance.get_audit_log(limit)}
