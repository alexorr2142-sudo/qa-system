"""Lab 4 adapter: connects the starter tests to this project's RAG pipeline in rag.py.

ask_chatbot() runs retrieval + generation live on every call. It deliberately does not go
through rag.ask(), because that returns cached answers (tests would pass without exercising
the system and latencies would be ~0 ms) and writes to cache/qa_cache.json.
"""
from functools import lru_cache

import rag


@lru_cache(maxsize=1)
def get_rag() -> rag.Retriever:
    """Load the chunk index (400-token chunks) and embeddings once and reuse them for all tests."""
    return rag.Retriever(rag.DEFAULT_SIZE)


def retrieve(question: str, top_k: int = rag.DEFAULT_K) -> list[tuple[dict, float]]:
    """Retrieval only (hybrid dense + BM25). Returns [(chunk, score), ...]."""
    if not isinstance(question, str):
        raise TypeError("Question must be a string.")
    question = question.strip()
    if not question:
        raise ValueError("Question cannot be empty.")
    return get_rag().search(question, k=top_k)


def ask_chatbot(question: str, top_k: int = rag.DEFAULT_K) -> str:
    """Full RAG pipeline: retrieval + Claude answer grounded in the retrieved chunks."""
    hits = retrieve(question, top_k=top_k)
    prompt = rag.build_prompt(question.strip(), hits)
    try:
        out = rag.ask_claude(*prompt)
    except rag.LLMError as e:
        if e.kind not in ("timeout", "cli_error"):
            raise
        out = rag.ask_claude(*prompt)  # same single retry as rag.ask()
    return out["text"]
