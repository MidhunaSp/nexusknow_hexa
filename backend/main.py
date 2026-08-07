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


class LoginRequest(BaseModel):
    username: str
    password: str


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
        governance.register_document(
            doc_id=result["doc_id"],
            filename=result["filename"],
            tags=tag_list,
            uploaded_by=user["username"],
            num_chunks=result["num_chunks"],
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {str(e)}")

    return {
        "doc_id": result["doc_id"],
        "filename": result["filename"],
        "num_chunks": result["num_chunks"],
        "tags": tag_list,
    }


@app.post("/query")
def query(req: QueryRequest, authorization: Optional[str] = Header(None)):
    """
    Ask a question. If a valid session is presented, the role is taken from the
    account server-side — the request body's role field is ignored, which is what
    prevents a client from just claiming to be HR. Without a session, falls back to
    the client-supplied user_id/role (demo mode), clearly tagged as such in the audit log.
    """
    user = auth.get_current_user(authorization)
    if user:
        user_id, role, auth_mode = user["username"], user["role"], "authenticated"
    else:
        user_id, role, auth_mode = req.user_id, req.role, "demo_fallback"

    allowed_tags = governance.get_allowed_tags(role)
    result = rag_engine.answer_query(req.query, allowed_tags=allowed_tags)

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


@app.get("/audit-log")
def get_audit_log(limit: int = 50):
    return {"log": governance.get_audit_log(limit)}
