"""Deterministic CI tests for the retrieval side of the RAG pipeline (MIS 7623 DevOps lab).

They use the committed chunk index and embeddings (cache/chunks_400.json, cache/emb_400.npy)
and the hybrid dense + BM25 search. No LLM call is made, so the results are reproducible in CI.
"""
from test_adapter import get_rag


def test_rag_builds_index():
    rag = get_rag()
    count = len(rag.chunks)
    assert count > 0
    assert rag.emb.shape[0] == count  # one embedding per chunk


def test_retrieval_returns_results():
    results = get_rag().search("AI software developer productivity", k=3)
    assert len(results) > 100


def test_top_k_is_respected():
    results = get_rag().search("software development", k=2)
    assert len(results) <= 2
