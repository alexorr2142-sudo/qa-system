"""Lab 4 functional tests for the RAG Q&A system.

Oracle: the source documents. Every expected keyword in test_cases.json was checked against the
extracted PDF text before the tests were run. Answers are LLM-generated, so checks are on
required concepts, citations and refusal behavior, never exact strings.

Tests marked `llm` call Claude (about 12 live calls, a few minutes). Run the fast subset with:
    pytest -v -m "not llm"
"""
import json
import re
from pathlib import Path

import pytest

import rag
from test_adapter import ask_chatbot, get_rag, retrieve

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / "test_cases.json").read_text(encoding="utf-8"))
BY_ID = {c["id"]: c for c in CASES}
NOT_FOUND = rag.NOT_FOUND.lower()
CITE = re.compile(r"\[([A-Za-z]+),\s*p\.?\s*(\d+)(?:\s*[-–]\s*(\d+))?\]")

_answers: dict[str, str] = {}


@pytest.fixture(scope="session", autouse=True)
def save_answers():
    """Keep the raw answers from each run as evidence for the testing report."""
    yield
    if _answers:
        out = {cid: {"question": BY_ID[cid]["question"], "answer": a} for cid, a in _answers.items()}
        (ROOT / "test_results").mkdir(exist_ok=True)
        (ROOT / "test_results" / "answers.json").write_text(json.dumps(out, indent=1), encoding="utf-8")


def answer_for(case: dict) -> str:
    """One live call per question per test session, shared by every test that checks that case."""
    if case["id"] not in _answers:
        _answers[case["id"]] = ask_chatbot(case["question"])
    return _answers[case["id"]]


def normalize(text: str) -> str:
    return " ".join(str(text).lower().split())


def missing_concepts(text: str, expected: list) -> list:
    """Each entry is a required concept: a string, or a list of acceptable alternatives."""
    text = normalize(text)
    groups = [[g] if isinstance(g, str) else g for g in expected]
    return [g for g in groups if not any(normalize(alt) in text for alt in g)]


def citations(text: str) -> list[tuple[str, int, int]]:
    return [(d, int(a), int(b or a)) for d, a, b in CITE.findall(text)]


def cases(*types: str) -> list:
    return [pytest.param(c, id=c["id"]) for c in CASES if c["type"] in types]


FACT_CASES = cases("supported", "paraphrase")


# ---------------------------------------------------------------- no LLM needed
@pytest.mark.parametrize("bad", ["", "   ", "\n\t"])
def test_blank_question_is_rejected(bad):
    with pytest.raises(ValueError):
        ask_chatbot(bad)


def test_non_string_question_is_rejected():
    with pytest.raises(TypeError):
        ask_chatbot(None)


@pytest.mark.parametrize("case", FACT_CASES)
def test_retrieval_finds_the_source_passage(case):
    """Retrieval-only check, so a failure can be classified as retrieval vs generation."""
    hits = retrieve(case["question"])
    key = case["expected_keywords"][0]
    alts = [key] if isinstance(key, str) else key
    found = [c for c, _ in hits if c["doc"] == case["expected_doc"] and any(normalize(a) in normalize(c["text"]) for a in alts)]
    assert found, f"No top-{len(hits)} chunk from {case['expected_doc']} contains {alts}; got {[c['doc'] for c, _ in hits]}"


# ---------------------------------------------------------------- full pipeline
@pytest.mark.llm
@pytest.mark.parametrize("case", cases(*{c["type"] for c in CASES}))
def test_chatbot_returns_nonempty_answer(case):
    answer = answer_for(case)
    assert isinstance(answer, str)
    assert answer.strip(), "The chatbot returned an empty answer."


@pytest.mark.llm
@pytest.mark.parametrize("case", FACT_CASES)
def test_supported_answers_include_expected_facts(case):
    answer = answer_for(case)
    assert NOT_FOUND not in normalize(answer), f"Refused a supported question: {answer}"
    missing = missing_concepts(answer, case["expected_keywords"])
    assert not missing, f"Missing verified concepts {missing} in: {answer}"


@pytest.mark.llm
@pytest.mark.parametrize("case", FACT_CASES)
def test_supported_answers_cite_the_source_document(case):
    answer = answer_for(case)
    docs = {d for d, _, _ in citations(answer)}
    assert case["expected_doc"] in docs, f"Expected a [{case['expected_doc']}, p.N] citation, got {docs or 'none'}: {answer}"


@pytest.mark.llm
@pytest.mark.parametrize("case", FACT_CASES)
def test_cited_pages_contain_the_fact(case):
    """Source-support oracle: the key figure must appear on a page the answer actually cites."""
    answer = answer_for(case)
    key = case["expected_keywords"][0]
    alts = [key] if isinstance(key, str) else key
    cited = citations(answer)
    chunks = get_rag().chunks
    supported = any(
        c["doc"] == doc and c["page_start"] <= end and c["page_end"] >= start
        and any(normalize(a) in normalize(c["text"]) for a in alts)
        for doc, start, end in cited for c in chunks
    )
    assert supported, f"None of the cited pages {cited} contain {alts}: {answer}"


@pytest.mark.llm
@pytest.mark.parametrize("case", cases("paraphrase"))
def test_paraphrases_give_consistent_answers(case):
    original = BY_ID[case["paraphrase_of"]]
    a, b = answer_for(original), answer_for(case)
    for text, which in ((a, original["id"]), (b, case["id"])):
        assert not missing_concepts(text, case["expected_keywords"]), f"{which} lacks {case['expected_keywords']}: {text}"
    docs_a = {d for d, _, _ in citations(a)}
    docs_b = {d for d, _, _ in citations(b)}
    assert docs_a & docs_b, f"Paraphrases cite different documents: {docs_a} vs {docs_b}"


@pytest.mark.llm
@pytest.mark.parametrize("case", cases("unsupported"))
def test_unsupported_question_is_handled_conservatively(case):
    """The system prompt defines the refusal as the exact sentence rag.NOT_FOUND, so check for it."""
    answer = normalize(answer_for(case))
    assert NOT_FOUND in answer, f"Expected '{rag.NOT_FOUND}' but got: {answer}"


@pytest.mark.llm
@pytest.mark.parametrize("case", cases("adversarial"))
def test_adversarial_question_does_not_fabricate(case):
    answer = answer_for(case)
    missing = missing_concepts(answer, case["expected_keywords"])
    assert not missing, f"Missing {missing}: {answer}"
    leaked = [f for f in case.get("forbidden_keywords", []) if normalize(f) in normalize(answer)]
    assert not leaked, f"Answer contains unsupported content {leaked}: {answer}"


@pytest.mark.llm
@pytest.mark.parametrize("case", cases("ambiguous"))
def test_ambiguous_question_attributes_every_answer(case):
    """Weak heuristic: figures must be attributed to a source. Human review still decides quality."""
    answer = answer_for(case)
    assert citations(answer) or NOT_FOUND in normalize(answer), f"No citation and no refusal: {answer}"
