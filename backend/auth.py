"""
Authentication Layer
- Real, server-enforced login: username + password -> session token, backed by SQLite.
- Role comes from the account record, never from the client. This is what closes the
  "just claim I'm HR" hole that a client-supplied role field (or a UI dropdown) leaves open.
- DEMO_ACCOUNTS below is a hardcoded credential store — a clear stand-in for a real
  identity provider (Google/Microsoft/Okta via OAuth2/OIDC), not production auth. Swapping
  in real SSO later only changes how identity is established; role-from-account and
  session enforcement here would stay the same.
"""
import sqlite3
import secrets
import bcrypt
from datetime import datetime, timedelta
from contextlib import contextmanager
from backend.config import settings

# ---- Hardcoded demo accounts (username -> (password, role)) ----
# Passwords are hashed at import time below; nothing is stored or compared in plaintext.
_DEMO_ACCOUNTS_PLAINTEXT = {
    "admin":     {"password": "Admin123!",          "role": "admin"},
    "jane.doe":  {"password": "HrPass123!",         "role": "hr"},
    "mike.chen": {"password": "FinancePass123!",    "role": "finance"},
    "alex.kim":  {"password": "EngPass123!",        "role": "engineering"},
    "sam.lee":   {"password": "EmployeePass123!",   "role": "employee"},
}

SESSION_EXPIRY_HOURS = settings.SESSION_EXPIRY_HOURS


def _hash(password: str) -> bytes:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())


# Built once at import time: username -> {"password_hash": bytes, "role": str}
DEMO_ACCOUNTS = {
    username: {"password_hash": _hash(info["password"]), "role": info["role"]}
    for username, info in _DEMO_ACCOUNTS_PLAINTEXT.items()
}


@contextmanager
def _get_db():
    conn = sqlite3.connect(settings.AUDIT_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_auth_db():
    with _get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                role TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
        """)


def authenticate(username: str, password: str) -> str | None:
    """Returns the account's role if the password is correct, else None."""
    account = DEMO_ACCOUNTS.get(username)
    if not account:
        return None
    if not bcrypt.checkpw(password.encode("utf-8"), account["password_hash"]):
        return None
    return account["role"]


def create_session(username: str, role: str) -> dict:
    token = secrets.token_urlsafe(32)
    now = datetime.utcnow()
    expires = now + timedelta(hours=SESSION_EXPIRY_HOURS)
    with _get_db() as conn:
        conn.execute(
            "INSERT INTO sessions (token, username, role, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
            (token, username, role, now.isoformat(), expires.isoformat()),
        )
    return {"token": token, "username": username, "role": role, "expires_at": expires.isoformat()}


def resolve_session(token: str) -> dict | None:
    """Returns {"username", "role"} for a valid, non-expired session token, else None."""
    if not token:
        return None
    with _get_db() as conn:
        row = conn.execute(
            "SELECT username, role, expires_at FROM sessions WHERE token = ?", (token,)
        ).fetchone()
    if not row:
        return None
    if datetime.fromisoformat(row["expires_at"]) < datetime.utcnow():
        delete_session(token)
        return None
    return {"username": row["username"], "role": row["role"]}


def delete_session(token: str) -> None:
    with _get_db() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


def get_current_user(authorization: str | None) -> dict | None:
    """
    Parses an `Authorization: Bearer <token>` header and resolves it to a session.
    Returns None (never raises) if the header is missing/malformed/expired — callers
    decide whether that means "reject" or "fall back to demo mode".
    """
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization[len("Bearer "):].strip()
    return resolve_session(token)
