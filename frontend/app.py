"""
Nexus Know — Streamlit Frontend
A dedicated login page gates the app. Real login: username+password against the
backend's session store, role comes from the account, never from anything picked in
the UI. The demo-account dropdown is a convenience for testing only — selecting one
still goes through the exact same real /auth/login call as typing credentials by hand,
it just fills them in for you.

"Continue without logging in" is an explicit, visibly-labeled fallback that behaves like
the old open demo (self-reported role, no server-side verification) — kept so the app is
still easy to poke at without credentials, but every response from that path is tagged
auth_mode: "demo_fallback" in the audit log so it can never be mistaken for a real session.
"""
import os
import json
import streamlit as st
import requests

API_URL = os.getenv("API_URL", "http://localhost:8000")

# Mirrors backend/auth.py's DEMO_ACCOUNTS — for the one-click dropdown only. Selecting an
# entry still calls the real /auth/login endpoint with these credentials; it does not
# bypass authentication in any way, it just saves you typing them.
DEMO_ACCOUNTS = {
    "-- choose a demo account --": None,
    "admin — Admin (sees everything)":              ("admin", "Admin123!"),
    "jane.doe — HR":                                  ("jane.doe", "HrPass123!"),
    "mike.chen — Finance":                            ("mike.chen", "FinancePass123!"),
    "alex.kim — Engineering":                         ("alex.kim", "EngPass123!"),
    "sam.lee — Employee (least privilege)":           ("sam.lee", "EmployeePass123!"),
}

st.set_page_config(page_title="Nexus Know", page_icon="🔗", layout="wide")

for key, default in [("auth_token", None), ("username", None), ("role", None),
                      ("demo_mode", False), ("messages", []), ("tags_field_value", "general"),
                      ("pending_tags_suggestion", None), ("tag_suggestion_reason", ""),
                      ("tag_suggestion_just_ran", False)]:
    if key not in st.session_state:
        st.session_state[key] = default

# Must happen before the tags_field_value widget is instantiated below: Streamlit forbids
# writing to a widget's session_state key after that widget has already been drawn in the
# same run. Staging the new value in a separate key and applying it here, at the top of
# the script (i.e. before that widget exists yet this run), is the standard workaround.
if st.session_state.pending_tags_suggestion is not None:
    st.session_state.tags_field_value = st.session_state.pending_tags_suggestion
    st.session_state.pending_tags_suggestion = None
    st.session_state.tag_suggestion_just_ran = True
    st.toast(f"🤖 AI suggested: {st.session_state.tags_field_value}", icon="🤖")


def auth_headers():
    if st.session_state.auth_token:
        return {"Authorization": f"Bearer {st.session_state.auth_token}"}
    return {}


def do_login(username, password):
    try:
        resp = requests.post(f"{API_URL}/auth/login",
                              json={"username": username, "password": password}, timeout=10)
        if resp.status_code == 200:
            session = resp.json()
            st.session_state.auth_token = session["token"]
            st.session_state.username = session["username"]
            st.session_state.role = session["role"]
            return True
        st.error(resp.json().get("detail", "Login failed."))
        return False
    except Exception as e:
        st.error(f"Could not reach backend: {e}")
        return False


def do_logout():
    try:
        requests.post(f"{API_URL}/auth/logout", headers=auth_headers(), timeout=10)
    except Exception:
        pass
    st.session_state.auth_token = None
    st.session_state.username = None
    st.session_state.role = None
    st.session_state.demo_mode = False
    st.session_state.messages = []


logged_in = st.session_state.auth_token is not None

# =========================================================================
# LOGIN PAGE — shown full-page instead of the app whenever there's no
# active session and demo mode hasn't been explicitly chosen.
# =========================================================================
if not logged_in and not st.session_state.demo_mode:
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,600&family=Inter:wght@400;500;600&display=swap');

    .stApp {
        background: radial-gradient(circle at 50% -10%, #16213A 0%, #0B1220 55%);
    }
    div[data-testid="stVerticalBlockBorderWrapper"] {
        background: #121A2B;
        border: 1px solid #263252 !important;
        border-radius: 18px;
        box-shadow: 0 24px 60px -12px rgba(0,0,0,0.5);
    }
    .nexus-nodes { display:flex; justify-content:center; margin-bottom: 6px; }
    .nexus-wordmark {
        font-family: 'Fraunces', serif; font-weight: 600; font-size: 2.6rem;
        text-align:center; color:#EDF1F9; margin: 0; letter-spacing: -0.01em;
    }
    .nexus-tagline {
        font-family:'Inter',sans-serif; text-align:center; color:#D9A857;
        font-size:0.72rem; letter-spacing:0.16em; text-transform:uppercase;
        margin-top:6px; margin-bottom:2.2rem;
    }
    .nexus-eyebrow {
        font-family:'Inter',sans-serif; font-size:0.7rem; letter-spacing:0.12em;
        text-transform:uppercase; color:#8A93A8; margin-bottom:2px;
    }
    .nexus-subtext { font-family:'Inter',sans-serif; color:#6C7690; font-size:0.85rem; }
    .nexus-divider {
        border:none; height:1px; margin: 1.6rem 0 1.1rem 0;
        background: linear-gradient(to right, transparent, #2C3A5E, transparent);
    }
    .stButton>button {
        font-family:'Inter',sans-serif; font-weight:500; border-radius:10px !important;
        border:1px solid #2C3A5E !important; background:transparent; color:#D7DDEA;
    }
    .stButton>button:hover { border-color:#D9A857 !important; color:#D9A857; }
    .stButton>button[kind="primary"] {
        background:#D9A857 !important; border:none !important; color:#1A1305 !important; font-weight:600;
    }
    .stButton>button[kind="primary"]:hover { background:#E6BC74 !important; }
    .stTextInput input, div[data-baseweb="select"] > div {
        background:#0E1524 !important; border-color:#263252 !important;
        border-radius:10px !important; color:#EDF1F9 !important;
    }
    .streamlit-expanderHeader { font-family:'Inter',sans-serif; color:#8A93A8 !important; font-size:0.85rem; }
    </style>
    """, unsafe_allow_html=True)

    _, center, _ = st.columns([1, 1.3, 1])
    with center:
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown("""
        <div class="nexus-nodes">
          <svg width="56" height="28" viewBox="0 0 56 28">
            <line x1="8" y1="20" x2="28" y2="8" stroke="#D9A857" stroke-width="1.2"/>
            <line x1="28" y1="8" x2="48" y2="20" stroke="#D9A857" stroke-width="1.2"/>
            <line x1="8" y1="20" x2="48" y2="20" stroke="#D9A857" stroke-width="1.2" opacity="0.4"/>
            <circle cx="8" cy="20" r="3.5" fill="#0B1220" stroke="#D9A857" stroke-width="1.4"/>
            <circle cx="48" cy="20" r="3.5" fill="#0B1220" stroke="#D9A857" stroke-width="1.4"/>
            <circle cx="28" cy="8" r="4" fill="#D9A857"/>
          </svg>
        </div>
        """, unsafe_allow_html=True)
        st.markdown("<p class='nexus-wordmark'>Nexus Know</p>", unsafe_allow_html=True)
        st.markdown("<p class='nexus-tagline'>Every Answer, Sourced</p>", unsafe_allow_html=True)

        with st.container(border=True):
            st.markdown("<p class='nexus-eyebrow'>Quick demo login</p>", unsafe_allow_html=True)
            st.markdown(
                "<p class='nexus-subtext'>Real login, real session — this just fills in a demo account for you.</p>",
                unsafe_allow_html=True,
            )
            choice = st.selectbox("Demo account", list(DEMO_ACCOUNTS.keys()), label_visibility="collapsed")
            if st.button("Log in with selected account", type="primary", use_container_width=True):
                creds = DEMO_ACCOUNTS.get(choice)
                if creds is None:
                    st.warning("Pick a demo account from the dropdown first.")
                else:
                    if do_login(*creds):
                        st.rerun()

        st.markdown("<div style='height:1.4rem'></div>", unsafe_allow_html=True)
        with st.expander("Or log in manually"):
            m_user = st.text_input("Username", key="manual_user")
            m_pass = st.text_input("Password", type="password", key="manual_pass")
            if st.button("Log in", use_container_width=True):
                if do_login(m_user, m_pass):
                    st.rerun()

        st.markdown("<hr class='nexus-divider'/>", unsafe_allow_html=True)
        st.markdown("<p class='nexus-subtext' style='text-align:center;'>Not ready to log in?</p>", unsafe_allow_html=True)
        if st.button("Continue without logging in (demo mode — not secure)", use_container_width=True):
            st.session_state.demo_mode = True
            st.rerun()

    st.stop()  # never render the app itself past this point without a session or demo mode

# =========================================================================
# MAIN APP — reached once logged in, or once "demo mode" was chosen
# =========================================================================
st.title("🔗 Nexus Know")
st.caption("Every Answer, Sourced. — Ask questions across company documents, grounded and cited.")

with st.sidebar:
    st.header("👤 Session")

    if logged_in:
        st.success(f"Logged in as **{st.session_state.username}** ({st.session_state.role})")
        st.caption("Role is enforced server-side from your account — it can't be changed here.")
        if st.button("Log out"):
            do_logout()
            st.rerun()
        user_id = st.session_state.username
        role = st.session_state.role

    else:  # demo_mode
        st.warning(
            "⚠️ **Demo mode — not a real login.** Role is self-reported and NOT verified. "
            "Anyone can claim any role."
        )
        user_id = st.text_input("User ID (demo)", value="demo_user")
        try:
            roles = requests.get(f"{API_URL}/roles").json()["roles"]
        except Exception:
            roles = ["employee", "hr", "finance", "engineering", "admin"]
        role = st.selectbox("Role (demo — self-reported)", roles,
                             index=roles.index("employee") if "employee" in roles else 0)
        if st.button("Log in instead"):
            st.session_state.demo_mode = False
            st.rerun()

tab_chat, tab_upload, tab_admin = st.tabs(["💬 Chat", "📤 Upload Documents", "🛡️ Governance / Audit"])

def render_citations(citations):
    with st.expander("📎 Sources"):
        for c in citations:
            verified = c.get("verified")
            badge = "✅" if verified is True else ("⚠️" if verified is False else "❔")
            st.markdown(f"{badge} **[{c['marker']}] {c['filename']}** (page {c['page']})")
            if verified is False:
                st.caption("⚠️ AI fact-check flagged this citation as a possible mismatch with the excerpt below.")
            st.caption(c["excerpt"])


def send_query(prompt: str):
    """Shared by the chat box and the AI-suggested question chips."""
    # last few turns, cleaned to role/content only, for follow-up question context
    history = [{"role": m["role"], "content": m["content"]} for m in st.session_state.messages[-6:]]

    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Searching documents and generating answer..."):
            try:
                resp = requests.post(
                    f"{API_URL}/query",
                    json={"query": prompt, "user_id": user_id or "demo_user",
                          "role": role or "employee", "history": history},
                    headers=auth_headers(),
                    timeout=60,
                )
                resp.raise_for_status()
                data = resp.json()
                st.markdown(data["answer"])
                if data["citations"]:
                    render_citations(data["citations"])
                st.session_state.messages.append({
                    "role": "assistant", "content": data["answer"], "citations": data["citations"]
                })
            except Exception as e:
                st.error(f"Error: {e}")


# ---------------- CHAT TAB ----------------
with tab_chat:
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("citations"):
                render_citations(msg["citations"])

    # AI-generated example questions, pulled from docs this role can actually see —
    # only shown before the conversation starts, so they don't clutter an ongoing chat.
    if not st.session_state.messages:
        try:
            resp = requests.get(f"{API_URL}/suggested-questions",
                                 params={"user_id": user_id or "demo_user", "role": role or "employee"},
                                 headers=auth_headers(), timeout=10)
            suggestions = resp.json().get("questions", [])[:4]
        except Exception:
            suggestions = []

        if suggestions:
            st.caption("💡 Try asking:")
            cols = st.columns(len(suggestions))
            for col, s in zip(cols, suggestions):
                with col:
                    if st.button(s["question"], key=f"suggest_{s['question']}", use_container_width=True):
                        send_query(s["question"])
                        st.rerun()

    if prompt := st.chat_input("Ask a question about company documents..."):
        send_query(prompt)

# ---------------- UPLOAD TAB (admin session only) ----------------
with tab_upload:
    if not (logged_in and role == "admin"):
        st.warning(
            "🔒 **Uploads require an admin login.** This is enforced by the backend "
            "regardless of what this page shows — even a modified request without a valid "
            "admin session will be rejected. Log in with the `admin` demo account to upload."
        )
    else:
        st.subheader("Upload a document into the knowledge base")
        uploaded_file = st.file_uploader("Choose a file", type=["pdf", "docx", "pptx", "txt", "md"])

        if "tags_field_value" not in st.session_state:
            st.session_state.tags_field_value = "general"

        col_tags, col_suggest = st.columns([4, 1])
        with col_tags:
            tags_input = st.text_input(
                "Tags (comma-separated — controls which roles can access this doc)",
                help="Examples: hr, finance, engineering, general. Use 'general' for company-wide docs.",
                key="tags_field_value",
            )
        with col_suggest:
            st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
            if st.button("🤖 Suggest", disabled=uploaded_file is None, use_container_width=True):
                with st.spinner("Reading document and asking the model..."):
                    try:
                        files = {"file": (uploaded_file.name, uploaded_file.getvalue())}
                        resp = requests.post(f"{API_URL}/suggest-tags", files=files,
                                              headers=auth_headers(), timeout=60)
                        resp.raise_for_status()
                        suggestion = resp.json()
                        # Stage it — cannot write tags_field_value directly here, since that
                        # widget has already been instantiated above in this same run.
                        st.session_state.pending_tags_suggestion = ", ".join(suggestion["tags"])
                        st.session_state.tag_suggestion_reason = suggestion.get("reasoning", "")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Tag suggestion failed: {e}")

        if st.session_state.get("tag_suggestion_just_ran"):
            st.success(
                f"🤖 **AI suggestion applied:** tags field set to "
                f"`{st.session_state.tags_field_value}` — {st.session_state.get('tag_suggestion_reason', '')}"
            )
            st.session_state.tag_suggestion_just_ran = False  # show this once, right after the click

        if uploaded_file and st.button("Ingest Document"):
            with st.spinner("Processing document (extracting, chunking, embedding, generating sample questions)..."):
                try:
                    files = {"file": (uploaded_file.name, uploaded_file.getvalue())}
                    data = {"tags": tags_input}
                    resp = requests.post(f"{API_URL}/ingest", files=files, data=data,
                                          headers=auth_headers(), timeout=120)
                    resp.raise_for_status()
                    result = resp.json()
                    st.success(
                        f"✅ Ingested **{result['filename']}** — {result['num_chunks']} chunks created, "
                        f"tagged: {', '.join(result['tags'])}"
                    )
                    if result.get("sample_questions"):
                        st.caption("🤖 AI-generated example questions for this document (shown to users in Chat):")
                        for q in result["sample_questions"]:
                            st.markdown(f"- {q}")
                    st.session_state.tag_suggestion_reason = None
                except Exception as e:
                    st.error(f"Ingestion failed: {e}")

        st.divider()
        st.subheader("Documents in Knowledge Base")
        try:
            docs = requests.get(f"{API_URL}/documents").json()["documents"]
            if docs:
                st.dataframe(docs, use_container_width=True)

                st.markdown("**Fix a document's tags** (e.g. an AI suggestion was too broad)")
                doc_options = {f"{d['filename']} ({d['doc_id'][:8]}...)": d for d in docs}
                selected_label = st.selectbox("Document", list(doc_options.keys()), key="retag_doc_select")
                selected_doc = doc_options[selected_label]
                current_tags = ", ".join(json.loads(selected_doc["tags"]) if isinstance(selected_doc["tags"], str) else selected_doc["tags"])

                col_retag_input, col_retag_btn, col_delete_btn = st.columns([3, 1, 1])
                with col_retag_input:
                    new_tags = st.text_input("New tags (comma-separated)", value=current_tags, key="retag_input")
                with col_retag_btn:
                    st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
                    if st.button("Update tags", use_container_width=True):
                        try:
                            tag_list = [t.strip() for t in new_tags.split(",") if t.strip()]
                            resp = requests.patch(f"{API_URL}/documents/{selected_doc['doc_id']}/tags",
                                                   json={"tags": tag_list}, headers=auth_headers(), timeout=30)
                            resp.raise_for_status()
                            st.success(f"✅ Updated {resp.json()['chunks_updated']} chunk(s) to tags: {', '.join(tag_list)}")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Retag failed: {e}")
                with col_delete_btn:
                    st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
                    if st.button("🗑️ Delete", use_container_width=True):
                        try:
                            resp = requests.delete(f"{API_URL}/documents/{selected_doc['doc_id']}",
                                                    headers=auth_headers(), timeout=30)
                            resp.raise_for_status()
                            st.success(f"✅ Deleted {resp.json()['chunks_deleted']} chunk(s)")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Delete failed: {e}")
            else:
                st.caption("No documents ingested yet.")
        except Exception as e:
            st.error(f"Could not load documents: {e}")

# ---------------- GOVERNANCE / AUDIT TAB ----------------
with tab_admin:
    st.subheader("🛡️ Query Audit Log")
    st.caption(
        "Every question asked, by whom, what was retrieved, whether access was blocked by RBAC, "
        "and whether it came from a real login or the demo fallback."
    )
    try:
        log = requests.get(f"{API_URL}/audit-log").json()["log"]
        if log:
            st.dataframe(log, use_container_width=True)
        else:
            st.caption("No queries logged yet.")
    except Exception as e:
        st.error(f"Could not load audit log: {e}")

    st.divider()
    st.subheader("Role → Access Policy")
    st.markdown("""
    | Role | Can access document tags |
    |---|---|
    | `admin` | Everything (`*`) |
    | `hr` | `hr`, `general` |
    | `finance` | `finance`, `general` |
    | `engineering` | `engineering`, `general` |
    | `employee` | `general` only |
    """)
    st.caption("Uploads are restricted to the `admin` role, enforced server-side.")