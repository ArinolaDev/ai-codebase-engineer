"""
Wraps Chroma (local, file-persisted vector DB) with a local embedding model
via sentence-transformers. No API key, no external calls — everything runs
on your machine. One Chroma collection per project, so projects don't
pollute each other's search results.
"""

import chromadb
from chromadb.utils import embedding_functions

from app.core.config import settings
from app.indexing.chunker import CodeChunk

# Small, fast, genuinely decent for code+text semantic search. Downloads
# once (~80MB) the first time it's used, then runs fully offline.
_EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"

_client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
_embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name=_EMBEDDING_MODEL_NAME
)


def _collection_name(project_id: str) -> str:
    return f"project_{project_id}"


def get_or_create_collection(project_id: str):
    return _client.get_or_create_collection(
        name=_collection_name(project_id),
        embedding_function=_embedding_fn,
    )


def index_chunks(project_id: str, chunks: list[CodeChunk]) -> int:
    if not chunks:
        return 0

    collection = get_or_create_collection(project_id)

    collection.upsert(
        ids=[c.chunk_id for c in chunks],
        documents=[c.text for c in chunks],
        metadatas=[
            {
                "file_path": c.file_path,
                "symbol_name": c.symbol_name,
                "symbol_type": c.symbol_type,
                "start_line": c.start_line,
                "end_line": c.end_line,
            }
            for c in chunks
        ],
    )
    return len(chunks)


def search(project_id: str, query: str, top_k: int = 5) -> list[dict]:
    collection = get_or_create_collection(project_id)
    results = collection.query(query_texts=[query], n_results=top_k)

    hits = []
    if not results["ids"][0]:
        return hits

    for i in range(len(results["ids"][0])):
        hits.append(
            {
                "chunk_id": results["ids"][0][i],
                "distance": results["distances"][0][i],
                "metadata": results["metadatas"][0][i],
                "snippet": results["documents"][0][i][:500],
            }
        )
    return hits


def reset_project_index(project_id: str) -> None:
    try:
        _client.delete_collection(_collection_name(project_id))
    except Exception:
        # Collection didn't exist yet (chromadb raises different exception
        # types across versions for this — ValueError in some, a chromadb-
        # specific NotFoundError in others). Either way, nothing to clean up.
        pass