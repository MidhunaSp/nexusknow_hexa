"""
RAG Engine — Agentic Retrieval (Groq)
- Gives the model a `search_documents` tool instead of doing one fixed retrieval pass
- The model decides how many searches it needs (different phrasings, follow-up angles,
  narrower/broader terms) before it has enough to answer, capped at MAX_SEARCH_ROUNDS
- Every search call is still RBAC-filtered server-side (backend.vector_store.semantic_search),
  so the tool can never surface content the user's role isn't allowed to see
- Every claim in the final answer must carry a [n] citation; citations returned to the UI
  are limited to excerpts actually referenced in the answer text
- Full search trace (queries issued + result counts) is returned for the audit log
- Resilient to Groq's occasional malformed tool-call generation (tool_use_failed): retries,
  then falls back to a plain single-search RAG pass rather than surfacing a 500 to the user
- Supports short conversation history so follow-up questions ("what about contractors?")
  resolve correctly instead of being treated as a brand-new, context-free question
- Runs a lightweight second pass to flag citations whose excerpt doesn't actually
  support the claim it's attached to
- Also provides two small, non-critical LLM-assisted helpers used at ingestion time:
  suggest_tags() and generate_sample_questions()
"""
import re
import json
from groq import Groq, BadRequestError
from backend.config import settings
from backend.vector_store import semantic_search

client = Groq(api_key=settings.GROQ_API_KEY)

MAX_HISTORY_MESSAGES = 6  # last ~3 user/assistant exchanges

SYSTEM_PROMPT = """You are an enterprise knowledge assistant. You answer employee questions
using ONLY content retrieved via the search_documents tool. Follow these rules strictly:

1. Use the search_documents tool to find relevant excerpts before answering. You may call it
   more than once if the first results are incomplete — e.g. to try a different phrasing, a
   narrower or broader term, or to chase down a follow-up detail the question implies. Don't
   call it more times than you need to; stop searching once you have enough to answer.
2. Only use information contained in retrieved excerpts. Do not use outside knowledge.
3. Every factual claim in your final answer must be followed by a citation marker like [1],
   [2], etc., matching the excerpt numbers shown to you in the tool results. If multiple
   excerpts support a claim, cite all of them, e.g. [1][3].
4. If, after searching, the excerpts don't contain enough information to answer, say so
   clearly — do not guess or fabricate an answer, and do not invent citation numbers.
5. Be concise and direct. Use plain language suitable for a business audience.
6. You may be shown a short history of the recent conversation, to understand follow-up
   questions like "what about contractors?" or "and last quarter?". Use it ONLY to
   understand what the current question is really asking — you must still ground every
   claim in freshly retrieved excerpts for the CURRENT question, not in the history text.
"""

SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "search_documents",
        "description": (
            "Search the enterprise knowledge base for excerpts relevant to a query. "
            "Results are automatically restricted to documents the current user's role is "
            "authorized to see. Returns the top matching excerpts, or a message saying none "
            "were found. Call again with a different query if you need more information."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query — a natural-language question or "
                                    "phrase to look up in the document collection.",
                }
            },
            "required": ["query"],
        },
    },
}

MAX_TOOL_CALL_RETRIES = 2  # extra attempts if Groq returns a tool_use_failed 400


def _is_tool_use_failed(err: BadRequestError) -> bool:
    try:
        body = err.body if isinstance(err.body, dict) else json.loads(str(err.body))
        return body.get("error", {}).get("code") == "tool_use_failed"
    except Exception:
        return "tool_use_failed" in str(err)


def _strip_json_fences(raw: str) -> str:
    return re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()


def _history_to_messages(history: list[dict] | None) -> list[dict]:
    """
    Cleans up client-supplied conversation history for inclusion in the prompt:
    - keeps only the last MAX_HISTORY_MESSAGES entries
    - keeps only role/content (drops citations, tool-call plumbing, etc.)
    - strips [n] citation markers from prior assistant turns, since those markers
      referred to a citation registry that no longer exists for this new turn
    """
    if not history:
        return []
    cleaned = []
    for h in history[-MAX_HISTORY_MESSAGES:]:
        role = h.get("role")
        content = (h.get("content") or "").strip()
        if role not in ("user", "assistant") or not content:
            continue
        if role == "assistant":
            content = re.sub(r"\[\d+\]", "", content)
        cleaned.append({"role": role, "content": content})
    return cleaned


def _format_new_hits(hits: list[dict], start_index: int) -> str:
    """Render newly-retrieved chunks as a numbered excerpt block for the tool result."""
    if not hits:
        return "No matching authorized documents were found for this query."
    blocks = []
    for offset, c in enumerate(hits):
        n = start_index + offset
        blocks.append(f"[{n}] Source: {c['filename']} (page {c['page']})\n{c['text']}")
    return "\n\n---\n\n".join(blocks)


def _build_citations(final_text: str, registry: list[dict]) -> list[dict]:
    """Only surface citations actually referenced in the answer text, in order."""
    cited_markers = sorted({int(n) for n in re.findall(r"\[(\d+)\]", final_text)})
    citations = []
    for n in cited_markers:
        if 1 <= n <= len(registry):
            c = registry[n - 1]
            citations.append({
                "marker": n,
                "filename": c["filename"],
                "page": c["page"],
                "excerpt": c["text"][:220] + ("..." if len(c["text"]) > 220 else ""),
                "doc_id": c["doc_id"],
            })
    return citations


def verify_citations(answer_text: str, citations: list[dict]) -> list[dict]:
    """
    Second-pass check: does each cited excerpt actually support the claim it's
    attached to in the answer? Adds a "verified" field to each citation:
    True/False if checked successfully, None if verification itself failed.
    Never raises — a failed verification pass just leaves citations unchecked
    rather than blocking the answer.
    """
    if not citations:
        return citations

    excerpt_block = "\n\n".join(f"[{c['marker']}] {c['excerpt']}" for c in citations)
    prompt = (
        f"Answer given to a user:\n{answer_text}\n\n"
        f"Cited source excerpts:\n{excerpt_block}\n\n"
        "For each numbered citation, judge whether that excerpt genuinely supports the "
        "specific claim in the answer attached to that citation number. Respond ONLY with "
        'JSON, no prose, no markdown fences: {"results": [{"marker": 1, "supported": true}]}'
    )
    verdicts = {}
    try:
        response = client.chat.completions.create(
            model=settings.GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
            temperature=0.0,
        )
        raw = _strip_json_fences(response.choices[0].message.content or "")
        data = json.loads(raw)
        verdicts = {
            int(r["marker"]): bool(r["supported"])
            for r in data.get("results", [])
            if "marker" in r and "supported" in r
        }
    except Exception:
        verdicts = {}

    for c in citations:
        c["verified"] = verdicts.get(c["marker"], None)
    return citations


def suggest_tags(text_sample: str, known_tags: list[str]) -> dict:
    """
    LLM-assisted tag suggestion for the admin upload flow. Non-critical: on any
    failure, falls back to ["general"] rather than blocking the upload.
    """
    known = sorted(set(known_tags) | {"general"})
    prompt = (
        "A new document is being uploaded to an enterprise knowledge base. "
        f"Existing tags in use: {', '.join(known)}.\n\n"
        f"Document excerpt:\n{text_sample[:2000]}\n\n"
        "Suggest which existing tag(s) this document belongs to (pick from the list "
        "above), or propose ONE new short lowercase tag only if none fit at all. "
        "Respond ONLY with JSON, no prose, no markdown fences: "
        '{"tags": ["tag1"], "reasoning": "one short sentence"}'
    )
    try:
        response = client.chat.completions.create(
            model=settings.GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0.2,
        )
        data = json.loads(_strip_json_fences(response.choices[0].message.content or ""))
        tags = [t.strip().lower() for t in data.get("tags", []) if isinstance(t, str) and t.strip()]
        return {"tags": tags or ["general"], "reasoning": data.get("reasoning", "")}
    except Exception as e:
        return {"tags": ["general"], "reasoning": f"AI suggestion unavailable ({e}); defaulted to general."}


def generate_sample_questions(text_sample: str, n: int = 3) -> list[str]:
    """
    LLM-generated example questions for a newly-uploaded document, shown in the
    Chat tab to make the knowledge base more discoverable. Non-critical: returns
    an empty list on failure rather than blocking ingestion.
    """
    prompt = (
        f"Here is an excerpt from a company document:\n\n{text_sample[:2000]}\n\n"
        f"Write exactly {n} short, natural questions an employee might ask that this "
        "document could answer. Respond ONLY with JSON, no prose, no markdown fences: "
        '{"questions": ["...", "...", "..."]}'
    )
    try:
        response = client.chat.completions.create(
            model=settings.GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=250,
            temperature=0.5,
        )
        data = json.loads(_strip_json_fences(response.choices[0].message.content or ""))
        questions = [q.strip() for q in data.get("questions", []) if isinstance(q, str) and q.strip()]
        return questions[:n]
    except Exception:
        return []


def _plain_rag_fallback(query: str, allowed_tags: list[str], top_k: int, reason: str,
                         history: list[dict] | None = None) -> dict:
    """
    Fallback path used when Groq's tool-calling keeps failing (known Llama tool_use_failed
    flakiness on Groq). Does one direct search + a plain (non-tool) completion instead of
    the agentic loop, so the query still gets answered instead of erroring out.
    """
    hits = semantic_search(query, top_k=top_k, allowed_tags=allowed_tags)
    search_trace = [{
        "round": 1, "query": query, "num_results": len(hits),
        "num_new_results": len(hits), "fallback_reason": reason,
    }]

    if not hits:
        return {
            "answer": "I couldn't find any relevant, authorized documents to answer this "
                      "question. This may be because no matching content exists, or you "
                      "don't have access to the relevant document collection.",
            "citations": [], "retrieved_doc_ids": [], "search_trace": search_trace,
        }

    excerpt_block = _format_new_hits(hits, 1)
    fallback_messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    fallback_messages.extend(_history_to_messages(history))
    fallback_messages.append({"role": "user", "content": (
        f"Question: {query}\n\nRetrieved excerpts:\n\n{excerpt_block}\n\n"
        "Answer the question using only these excerpts, with [n] citation markers."
    )})
    response = client.chat.completions.create(
        model=settings.GROQ_MODEL, messages=fallback_messages, max_tokens=1024, temperature=0.3,
    )
    final_text = (response.choices[0].message.content or "").strip() or (
        "I wasn't able to find enough authorized information to answer this question."
    )
    citations = verify_citations(final_text, _build_citations(final_text, hits))
    return {
        "answer": final_text,
        "citations": citations,
        "retrieved_doc_ids": [c["doc_id"] for c in hits],
        "search_trace": search_trace,
    }


def answer_query(query: str, allowed_tags: list[str], top_k: int = None,
                  history: list[dict] | None = None) -> dict:
    """
    Agentic RAG pipeline: model searches (possibly multiple times) -> answers with citations.
    `history` is an optional list of {"role": "user"/"assistant", "content": str} from the
    recent conversation, used so follow-up questions resolve correctly.
    Returns the answer, UI citation list, retrieved doc ids (for ACL/audit), and a search trace.
    Falls back to a plain single-search pass if Groq's tool-calling repeatedly fails.
    """
    registry: list[dict] = []
    seen_chunk_ids: dict[str, int] = {}
    search_trace: list[dict] = []

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(_history_to_messages(history))
    messages.append({"role": "user", "content": f"Question: {query}"})

    rounds_used = 0
    final_text = None

    while True:
        rounds_used += 1
        force_final = rounds_used > settings.MAX_SEARCH_ROUNDS

        if force_final:
            messages.append({
                "role": "user",
                "content": (
                    "You've reached the search limit for this question. Answer now using only "
                    "the excerpts already retrieved above, with [n] citations, or state clearly "
                    "that the available information is insufficient."
                ),
            })
            kwargs = dict(model=settings.GROQ_MODEL, messages=messages, max_tokens=1024)
        else:
            kwargs = dict(
                model=settings.GROQ_MODEL,
                messages=messages,
                tools=[SEARCH_TOOL],
                tool_choice="auto",
                max_tokens=1024,
                temperature=0.2,  # lower temp reduces malformed tool-call generation on Groq
            )

        # Retry loop for Groq's occasional tool_use_failed 400s; if it never recovers,
        # abandon the agentic loop entirely and fall back to a plain single-search pass.
        response = None
        last_err = None
        for attempt in range(MAX_TOOL_CALL_RETRIES + 1):
            try:
                response = client.chat.completions.create(**kwargs)
                break
            except BadRequestError as e:
                last_err = e
                if not _is_tool_use_failed(e):
                    raise
                continue
        if response is None:
            return _plain_rag_fallback(query, allowed_tags, top_k, reason=str(last_err), history=history)

        msg = response.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None) or []

        if not tool_calls or force_final:
            final_text = (msg.content or "").strip() or (
                "I wasn't able to find enough authorized information to answer this question."
            )
            break

        # Model wants to search — run every requested search, append results, loop back
        messages.append({
            "role": "assistant",
            "content": msg.content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in tool_calls
            ],
        })

        for tc in tool_calls:
            try:
                args = json.loads(tc.function.arguments)
            except (json.JSONDecodeError, TypeError):
                args = {}
            search_query = args.get("query", query)
            hits = semantic_search(search_query, top_k=top_k, allowed_tags=allowed_tags)

            new_hits = []
            for h in hits:
                if h["chunk_id"] not in seen_chunk_ids:
                    marker = len(registry) + 1
                    seen_chunk_ids[h["chunk_id"]] = marker
                    registry.append(h)
                    new_hits.append(h)

            start_index = seen_chunk_ids[new_hits[0]["chunk_id"]] if new_hits else len(registry) + 1
            result_text = _format_new_hits(new_hits, start_index) if new_hits else (
                "No new matching authorized documents were found for this query "
                "(they may have already been shown above)." if hits else
                "No matching authorized documents were found for this query."
            )

            search_trace.append({
                "round": rounds_used,
                "query": search_query,
                "num_results": len(hits),
                "num_new_results": len(new_hits),
            })

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result_text,
            })

    if not registry:
        return {
            "answer": "I couldn't find any relevant, authorized documents to answer this "
                      "question. This may be because no matching content exists, or you "
                      "don't have access to the relevant document collection.",
            "citations": [],
            "retrieved_doc_ids": [],
            "search_trace": search_trace,
        }

    citations = verify_citations(final_text, _build_citations(final_text, registry))
    return {
        "answer": final_text,
        "citations": citations,
        "retrieved_doc_ids": [c["doc_id"] for c in registry],
        "search_trace": search_trace,
    }
