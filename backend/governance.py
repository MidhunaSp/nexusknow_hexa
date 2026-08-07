"""
Knowledge Governance Layer
- Role-based access control (RBAC) on document collections
- Audit logging of every query: who asked what, what was retrieved, what was answered
- This is the piece that turns a "RAG demo" into an "Enterprise RAG platform"
"""
import sqlite3
import json
from datetime import datetime
from contextlib import contextmanager
from backend.config import settings

# ---- Simple role -> allowed document tags mapping ----
# In a real system this would live in a proper user/auth DB (e.g. tied to SSO/LDAP groups).
# Here it's a lightweight in-memory policy table so the concept is easy to demo and extend.
ROLE_POLICIES = {
    "admin":   {"allowed_tags": ["*"]},                       # sees everything
    "hr":      {"allowed_tags": ["hr", "general"]},
    "finance": {"allowed_tags": ["finance", "general"]},
    "engineering": {"allowed_tags": ["engineering", "general"]},
    "employee": {"allowed_tags": ["general"]},                 # default, least privilege
}


def get_allowed_tags(role: str) -> list[str]:
    policy = ROLE_POLICIES.get(role, ROLE_POLICIES["employee"])
    return policy["allowed_tags"]


def can_access(role: str, doc_tags: list[str]) -> bool:
    """Check whether a given role can access a document with the given tags."""
    allowed = get_allowed_tags(role)
    if "*" in allowed:
        return True
    return any(tag in allowed for tag in doc_tags)


@contextmanager
def _get_db():
    conn = sqlite3.connect(settings.AUDIT_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_audit_db():
    with _get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                user_id TEXT NOT NULL,
                role TEXT NOT NULL,
                query TEXT NOT NULL,
                retrieved_doc_ids TEXT,
                answer TEXT,
                blocked_by_acl INTEGER DEFAULT 0,
                search_trace TEXT,
                auth_mode TEXT DEFAULT 'demo_fallback'
            )
        """)
        # Migration for DBs created before search_trace/auth_mode existed
        existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(audit_log)")}
        if "search_trace" not in existing_cols:
            conn.execute("ALTER TABLE audit_log ADD COLUMN search_trace TEXT")
        if "auth_mode" not in existing_cols:
            conn.execute("ALTER TABLE audit_log ADD COLUMN auth_mode TEXT DEFAULT 'demo_fallback'")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                doc_id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                tags TEXT NOT NULL,
                uploaded_by TEXT,
                uploaded_at TEXT,
                num_chunks INTEGER
            )
        """)


def log_query(user_id: str, role: str, query: str, retrieved_doc_ids: list[str],
              answer: str, blocked_by_acl: bool = False, search_trace: list[dict] = None,
              auth_mode: str = "demo_fallback"):
    with _get_db() as conn:
        conn.execute(
            """INSERT INTO audit_log
               (timestamp, user_id, role, query, retrieved_doc_ids, answer, blocked_by_acl, search_trace, auth_mode)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (datetime.utcnow().isoformat(), user_id, role, query,
             json.dumps(retrieved_doc_ids), answer, int(blocked_by_acl),
             json.dumps(search_trace or []), auth_mode)
        )


def register_document(doc_id: str, filename: str, tags: list[str], uploaded_by: str, num_chunks: int):
    with _get_db() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO documents
               (doc_id, filename, tags, uploaded_by, uploaded_at, num_chunks)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (doc_id, filename, json.dumps(tags), uploaded_by,
             datetime.utcnow().isoformat(), num_chunks)
        )


def get_document_tags(doc_id: str) -> list[str]:
    with _get_db() as conn:
        row = conn.execute("SELECT tags FROM documents WHERE doc_id = ?", (doc_id,)).fetchone()
        return json.loads(row["tags"]) if row else []


def list_documents():
    with _get_db() as conn:
        rows = conn.execute("SELECT * FROM documents ORDER BY uploaded_at DESC").fetchall()
        return [dict(r) for r in rows]


def get_audit_log(limit: int = 100):
    with _get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
