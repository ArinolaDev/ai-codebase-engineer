"""
Codebase Q&A: given a question, retrieve the most relevant code chunks
(Phase 3's semantic search) and ask the LLM to answer using ONLY that
retrieved context — this is what keeps answers grounded in the actual
repo instead of the model guessing/hallucinating.
"""

from app.indexing.vector_store import search
from app.core import llm_client


SYSTEM_PROMPT = """You are a code assistant answering questions about a specific codebase.
You will be given relevant code snippets retrieved from the project, followed by a question.

Rules:
- Answer ONLY using the information in the provided code snippets.
- If the snippets don't contain enough information to answer, say so clearly — do not guess or make up details.
- When you reference a function or class, mention its file and line numbers.
- Some snippets are "module-level code": each of their lines starts with its real line number
  (for example `10: db_password = "x"`). When you cite one of those lines, use exactly that number.
- Refer to files by the short path shown in each snippet header, never by a longer folder path.
- Be concise and direct.
"""


def _short_path(path: str, project_id: str) -> str:
    """Show 'pkg/mod.py' instead of 'C:\\Users\\...\\data\\repos\\<id>\\pkg\\mod.py', so the model
    doesn't repeat a long absolute path in its answer."""
    p = str(path).replace("\\", "/")
    marker = f"/{project_id}/"
    i = p.find(marker)
    return p[i + len(marker):] if i >= 0 else p


def _build_prompt(question: str, hits: list[dict], project_id: str = "") -> str:
    if not hits:
        context = "(No relevant code was found in the index for this question.)"
    else:
        context_blocks = []
        for h in hits:
            meta = h["metadata"]
            short = _short_path(meta["file_path"], project_id)
            # the chunk text repeats the absolute path in its own first line: shorten that too
            snippet = h["snippet"].replace(meta["file_path"], short)
            context_blocks.append(
                f"--- {meta['symbol_type']} `{meta['symbol_name']}` "
                f"in {short} (lines {meta['start_line']}-{meta['end_line']}) ---\n"
                f"{snippet}"
            )
        context = "\n\n".join(context_blocks)

    return (
        f"{SYSTEM_PROMPT}\n\n"
        f"=== Retrieved code context ===\n{context}\n\n"
        f"=== Question ===\n{question}\n\n"
        f"=== Answer ===\n"
    )


# How much of each retrieved chunk the model gets to see. The search endpoint shows a short
# preview (500 chars); chat needs more, or a line near the end of a chunk (like a password
# constant) is cut off and the model correctly says it can't find it.
CHAT_SNIPPET_CHARS = 1600


def answer_question(project_id: str, question: str, top_k: int = 5, model: str | None = None) -> dict:
    hits = search(project_id, question, top_k=top_k, snippet_chars=CHAT_SNIPPET_CHARS)
    prompt = _build_prompt(question, hits, project_id)
    answer_text = (llm_client.llm_for(model) or llm_client.generate)(prompt)

    sources = [
        {
            "file_path": h["metadata"]["file_path"],
            "symbol_name": h["metadata"]["symbol_name"],
            "start_line": h["metadata"]["start_line"],
            "end_line": h["metadata"]["end_line"],
        }
        for h in hits
    ]

    return {"answer": answer_text, "sources": sources}
