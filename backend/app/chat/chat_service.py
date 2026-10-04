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
- Be concise and direct.
"""


def _build_prompt(question: str, hits: list[dict]) -> str:
    if not hits:
        context = "(No relevant code was found in the index for this question.)"
    else:
        context_blocks = []
        for h in hits:
            meta = h["metadata"]
            context_blocks.append(
                f"--- {meta['symbol_type']} `{meta['symbol_name']}` "
                f"in {meta['file_path']} (lines {meta['start_line']}-{meta['end_line']}) ---\n"
                f"{h['snippet']}"
            )
        context = "\n\n".join(context_blocks)

    return (
        f"{SYSTEM_PROMPT}\n\n"
        f"=== Retrieved code context ===\n{context}\n\n"
        f"=== Question ===\n{question}\n\n"
        f"=== Answer ===\n"
    )


def answer_question(project_id: str, question: str, top_k: int = 5) -> dict:
    hits = search(project_id, question, top_k=top_k)
    prompt = _build_prompt(question, hits)
    answer_text = llm_client.generate(prompt)

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