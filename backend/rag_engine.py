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
"""
import re
import json
from groq import Groq, BadRequestError
from backend.config import settings
from backend.vector_store import semantic_search

client = Groq(api_key=settings.GROQ_API_KEY)

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


def _plain_rag_fallback(query: str, allowed_tags: list[str], top_k: int, reason: str) -> dict:
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
    fallback_messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"Question: {query}\n\nRetrieved excerpts:\n\n{excerpt_block}\n\n"
            "Answer the question using only these excerpts, with [n] citation markers."
        )},
    ]
    response = client.chat.completions.create(
        model=settings.GROQ_MODEL, messages=fallback_messages, max_tokens=1024, temperature=0.3,
    )
    final_text = (response.choices[0].message.content or "").strip() or (
        "I wasn't able to find enough authorized information to answer this question."
    )
    return {
        "answer": final_text,
        "citations": _build_citations(final_text, hits),
        "retrieved_doc_ids": [c["doc_id"] for c in hits],
        "search_trace": search_trace,
    }


def answer_query(query: str, allowed_tags: list[str], top_k: int = None) -> dict:
    """
    Agentic RAG pipeline: model searches (possibly multiple times) -> answers with citations.
    Returns the answer, UI citation list, retrieved doc ids (for ACL/audit), and a search trace.
    Falls back to a plain single-search pass if Groq's tool-calling repeatedly fails.
    """
    registry: list[dict] = []
    seen_chunk_ids: dict[str, int] = {}
    search_trace: list[dict] = []

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Question: {query}"},
    ]

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
            return _plain_rag_fallback(query, allowed_tags, top_k, reason=str(last_err))

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

    return {
        "answer": final_text,
        "citations": _build_citations(final_text, registry),
        "retrieved_doc_ids": [c["doc_id"] for c in registry],
        "search_trace": search_trace,
    }
