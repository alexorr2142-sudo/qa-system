"""RAG Q&A over the PDFs in ./data.

Pipeline: extract (pdftotext or OCR) -> clean -> chunk -> embed (bge-small) -> retrieve -> prompt Claude.
Run `python rag.py --help` for the CLI. `app.py` is the Streamlit front end; both call `ask()`.
"""
from __future__ import annotations

import argparse, hashlib, json, os, re, signal, statistics, subprocess, tempfile, time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------- constants
ROOT = Path(__file__).resolve().parent
DATA_DIR, CACHE_DIR = ROOT / "data", ROOT / "cache"
EMBED_MODEL = "BAAI/bge-small-en-v1.5"
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
OCR_DPI = 200
CLAUDE_MODEL = "sonnet"
DEFAULT_SIZE, DEFAULT_K = 400, 5
OCR_FILES = {"How-Anthropic-teams-use-Claude-Code_v2pdf.pdf", "AI Impact on Software Engineers 2026.pdf"}
SHORT_NAMES = {
    "MEASUR~1.PDF": "METR",
    "state-of-code-developer-survey-report.pdf": "Sonar",
    "How-Anthropic-teams-use-Claude-Code_v2pdf.pdf": "ClaudeCode",
    "the-state-of-enterprise-ai_2025-report.pdf": "OpenAI",
    "AI Impact on Software Engineers 2026.pdf": "Pragmatic",
    "Economics of AI Jobs - April 2026.pdf": "Econ",
}
DOC_NOTES = {"Pragmatic": "Paywalled article; PDF ends inside section 5 of 7. Sections 5-7 are absent."}
NOT_FOUND = "Not found in the documents."

SYSTEM_PROMPT = f"""You answer questions using ONLY the numbered sources provided in the user message.

Rules:
1. Every factual claim must end with a citation in the exact form [<doc>, p.<page>], copying <doc> and the page (or page range, e.g. p.8-10) exactly as written in the source header it came from. Example: [Sonar, p.14]. Never include the source number, e.g. not [1, Sonar, p.14] and not [1, p.14].
2. If the sources do not contain the answer, reply with exactly: {NOT_FOUND}
   Then add one sentence describing the closest related information in the sources, with a citation.
3. Do not use outside knowledge. Do not guess or round numbers; quote figures as they appear.
4. If two sources disagree, say so and cite both.
5. Keep the answer under 150 words unless the question asks to compare several documents.
6. Plain prose or short bullets. No preamble, no restating the question."""


# ---------------------------------------------------------------- extraction
def _run(cmd: list[str]) -> str:
    return subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True).stdout


def extract_pages(pdf: Path) -> list[str]:
    """One string per page. pdftotext for born-digital PDFs, tesseract for the two scanned ones. Cached on mtime."""
    cache = CACHE_DIR / f"pages_{pdf.stem}.json"
    if cache.exists():
        d = json.loads(cache.read_text())
        if d["mtime"] == pdf.stat().st_mtime:
            return d["pages"]
    n_pages = int(re.search(r"Pages:\s+(\d+)", _run(["pdfinfo", str(pdf)])).group(1))
    if pdf.name in OCR_FILES:
        pages = []
        with tempfile.TemporaryDirectory() as tmp:
            _run(["pdftoppm", "-r", str(OCR_DPI), "-png", str(pdf), f"{tmp}/pg"])
            for png in sorted(Path(tmp).glob("pg-*.png")):
                pages.append(_run(["tesseract", str(png), "-", "--psm", "3"]))
    else:
        pages = _run(["pdftotext", str(pdf), "-"]).split("\f")[:n_pages]
    pages += [""] * (n_pages - len(pages))
    CACHE_DIR.mkdir(exist_ok=True)
    cache.write_text(json.dumps({"mtime": pdf.stat().st_mtime, "pages": pages}))
    return pages


# ---------------------------------------------------------------- cleaning
_ALPHA = re.compile(r"[A-Za-z]{2,}")
_NUMERIC_ONLY = re.compile(r"^[\d%,.$x+\-–—/()\s]*$")


def _line_key(line: str) -> str:
    return re.sub(r"\d+", "#", re.sub(r"\s+", " ", line.strip().lower()))


def clean_pages(pages: list[str]) -> list[str]:
    """Generic cleaner. Returns same-length list; dropped pages become ''."""
    n = len(pages)
    # 1. page furniture: short lines that recur on >=30% of pages
    furniture: set[str] = set()
    if n >= 5:
        seen: dict[str, int] = {}
        for p in pages:
            for key in {_line_key(l) for l in p.splitlines() if l.strip() and len(l.split()) <= 12}:
                seen[key] = seen.get(key, 0) + 1
        furniture = {k for k, c in seen.items() if c >= 0.3 * n}
    out, in_refs = [], False
    for p in pages:
        # a reference block ends at the first page that no longer looks like a bibliography
        if in_refs and len(re.findall(r"^\s*\[\d+\]|https?://|doi\.org|arxiv|et al\.", p, re.M)) < 3:
            in_refs = False
        kept = []
        for line in p.splitlines():
            s = line.strip()
            if not s or _line_key(s) in furniture:
                kept.append("") if not s else None
                continue
            low = s.lower()
            # 4. reference list / acknowledgements until an appendix heading
            if re.fullmatch(r"(references|bibliography|acknowledg\w+)\.?", low):
                in_refs = True
                continue
            if in_refs:
                continue
            # 2. numeric residue (chart axes, table dumps); always keep real sentences
            toks = s.split()
            alpha = len(_ALPHA.findall(s))
            if alpha < 4 and (_NUMERIC_ONLY.match(s) or (len(toks) >= 3 and sum(bool(re.search(r"\d", t)) for t in toks) / len(toks) >= 0.6)):
                continue
            kept.append(s)
        text = "\n".join(kept)
        # 5. fused footnote digits: "developers.3 The" -> "developers. The"
        text = re.sub(r"([a-z.,])(\d{1,2})\b(?=\s)", r"\1", text)
        # 7. collapse whitespace, drop identical consecutive paragraphs
        paras, prev = [], None
        for para in re.split(r"\n\s*\n", text):
            para = para.strip()
            if para and para != prev:
                paras.append(para)
            prev = para or prev
        text = "\n\n".join(paras)
        # 3. low-content pages (cover, ToC, integer tables)
        words = text.split()
        alpha_words = len(_ALPHA.findall(text))
        if alpha_words < 40 or (words and alpha_words / len(words) < 0.5):
            text = ""
        out.append(text)
    return out


# ---------------------------------------------------------------- chunking
def _load_local_first(loader):
    """Use the Hugging Face cache without touching the network; download only if it is missing."""
    try:
        return loader(EMBED_MODEL, local_files_only=True)
    except Exception:
        return loader(EMBED_MODEL)


_tok = None


def n_tokens(text: str) -> int:
    global _tok
    if _tok is None:
        from transformers import AutoTokenizer
        _tok = _load_local_first(AutoTokenizer.from_pretrained)
    return len(_tok.encode(text, add_special_tokens=False))


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])", text) if s.strip()]


def chunk(pages: list[str], doc: str, file: str, target: int, cap: int, overlap: int = 50) -> list[dict]:
    """Paragraph-aware greedy packing. Overlap = trailing sentences of the previous chunk."""
    paras: list[dict] = []
    for pno, page in enumerate(pages, 1):
        for block in re.split(r"\n\s*\n", page):
            t = " ".join(l.strip() for l in block.splitlines() if l.strip())
            if not t:
                continue
            # micro-paragraphs (headings, labels) glue onto the next paragraph
            if paras and paras[-1]["micro"] and paras[-1]["pe"] == pno:
                paras[-1].update(text=paras[-1]["text"] + " " + t, micro=False)
                continue
            paras.append({"text": t, "ps": pno, "pe": pno, "micro": len(t.split()) < 8 and not t.rstrip().endswith((".", "!", "?", ":"))})
    # 6. cross-page join: sentence continues onto the next page
    joined: list[dict] = []
    for p in paras:
        if joined and joined[-1]["pe"] == p["ps"] - 1 and not joined[-1]["text"].rstrip().endswith((".", "!", "?", ":", '"', "”")) and p["text"][:1].islower():
            joined[-1].update(text=joined[-1]["text"] + " " + p["text"], pe=p["pe"])
        else:
            joined.append(p)
    # split oversize paragraphs on sentence boundaries
    units: list[dict] = []
    for p in joined:
        if n_tokens(p["text"]) <= cap - overlap:
            units.append(p)
            continue
        cur: list[str] = []
        for s in _sentences(p["text"]):
            if cur and n_tokens(" ".join(cur + [s])) > cap - overlap:
                units.append({**p, "text": " ".join(cur)})
                cur = []
            cur.append(s)
        if cur:
            units.append({**p, "text": " ".join(cur)})
    # greedy pack to target, hard cap
    chunks: list[dict] = []
    cur, cur_tok, carry = [], 0, None
    def flush():
        nonlocal cur, cur_tok, carry
        if not cur:
            return
        text = "\n\n".join(u["text"] for u in cur)
        chunks.append({"id": f"{doc}-{len(chunks):03d}", "doc": doc, "file": file, "page_start": min(u["ps"] for u in cur), "page_end": max(u["pe"] for u in cur), "text": text, "n_tokens": n_tokens(text)})
        tail, tail_tok = [], 0
        for s in reversed(_sentences(text)):
            if tail_tok + n_tokens(s) > overlap:
                break
            tail.insert(0, s); tail_tok += n_tokens(s)
        carry = {"text": " ".join(tail), "ps": cur[-1]["pe"], "pe": cur[-1]["pe"]} if tail else None
        cur, cur_tok = [], 0
    for u in units:
        ut = n_tokens(u["text"])
        if cur and (cur_tok >= target or cur_tok + ut > cap):
            flush()
        if not cur and carry and overlap:
            cur, cur_tok = [carry], n_tokens(carry["text"])
        cur.append(u); cur_tok += ut
    flush()
    return chunks


def _cap_for(size: int) -> int:
    return size if size >= 512 else min(int(size * 1.28), 512)


def load_docs() -> list[tuple[str, Path, list[str]]]:
    docs = []
    for pdf in sorted(DATA_DIR.iterdir()):
        if pdf.suffix.lower() == ".pdf" and pdf.name in SHORT_NAMES:
            docs.append((SHORT_NAMES[pdf.name], pdf, clean_pages(extract_pages(pdf))))
    return docs


def build(sizes: list[int]) -> None:
    docs = load_docs()
    for f in CACHE_DIR.glob("qa_cache.json"):
        f.unlink()
    for size in sizes:
        cap = _cap_for(size)
        if cap > 512:
            print(f"WARNING size {size}: cap {cap} exceeds bge-small's 512-token window; embeddings are truncated.")
        all_chunks = [c for doc, pdf, pages in docs for c in chunk(pages, doc, pdf.name, size, cap)]
        (CACHE_DIR / f"chunks_{size}.json").write_text(json.dumps(all_chunks, indent=1))
        toks = sorted(c["n_tokens"] for c in all_chunks)
        per_doc = {d: sum(c["doc"] == d for c in all_chunks) for d, _, _ in docs}
        print(f"\n== size {size} (cap {cap}): {len(all_chunks)} chunks | mean {statistics.mean(toks):.0f} median {statistics.median(toks):.0f} p90 {toks[int(.9 * len(toks)) - 1]} max {toks[-1]}")
        print("   per doc:", ", ".join(f"{d}={n}" for d, n in per_doc.items()))
        for lo in range(0, cap + 1, max(64, cap // 8)):
            n = sum(lo <= t < lo + max(64, cap // 8) for t in toks)
            print(f"   {lo:4d}-{lo + max(64, cap // 8) - 1:4d} {'#' * (n // 2)}{n and ' ' or ''}{n or ''}")
        Retriever(size)  # embed now so the first query is fast
    for stale in CACHE_DIR.glob("eval_*.md"):
        stale.unlink()


# ---------------------------------------------------------------- retrieval
def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+(?:[.,][0-9]+)?%?", text.lower())


class Retriever:
    _model = None

    def __init__(self, size: int = DEFAULT_SIZE):
        self.size = size
        self.chunks = json.loads((CACHE_DIR / f"chunks_{size}.json").read_text())
        emb_path = CACHE_DIR / f"emb_{size}.npy"
        if emb_path.exists() and (emb := np.load(emb_path)).shape[0] == len(self.chunks):
            self.emb = emb
        else:
            self.emb = self.model.encode([c["text"] for c in self.chunks], normalize_embeddings=True, batch_size=32, show_progress_bar=False).astype(np.float32)
            np.save(emb_path, self.emb)
        from rank_bm25 import BM25Okapi
        self.bm25 = BM25Okapi([_words(c["text"]) for c in self.chunks])

    @property
    def model(self):
        if Retriever._model is None:
            from sentence_transformers import SentenceTransformer
            Retriever._model = _load_local_first(SentenceTransformer)
        return Retriever._model

    def search(self, query: str, k: int = DEFAULT_K) -> list[tuple[dict, float]]:
        """Hybrid: dense cosine + BM25, fused by reciprocal rank (RRF). Score = fused RRF score."""
        q = self.model.encode([BGE_QUERY_PREFIX + query], normalize_embeddings=True)[0]
        dense = np.argsort(-(self.emb @ q))[:2 * k]
        lexical = np.argsort(-self.bm25.get_scores(_words(query)))[:2 * k]
        fused: dict[int, float] = {}
        for ranked in (dense, lexical):
            for rank, i in enumerate(ranked):
                fused[int(i)] = fused.get(int(i), 0.0) + 1.0 / (60 + rank)
        top = sorted(fused, key=fused.get, reverse=True)[:k]
        return [(self.chunks[i], fused[i]) for i in top]


# ---------------------------------------------------------------- LLM
class LLMError(RuntimeError):
    def __init__(self, kind: str, msg: str):
        super().__init__(f"{kind}: {msg}"); self.kind = kind


def _pages(c: dict) -> str:
    return f"p.{c['page_start']}" + (f"-{c['page_end']}" if c["page_end"] != c["page_start"] else "")


def build_prompt(question: str, hits: list[tuple[dict, float]]) -> tuple[str, str]:
    src = "\n\n".join(f"[{i}] {c['doc']}, {_pages(c)}\n{c['text']}" for i, (c, _) in enumerate(hits, 1))
    return SYSTEM_PROMPT, f"Sources:\n\n{src}\n\nQuestion: {question}"


def ask_claude(system: str, user: str, model: str = CLAUDE_MODEL, timeout: float = 90) -> dict:
    """Headless Claude Code call with tools disabled. Returns {text, usage, duration_ms}.

    The CLI is started in its own process group so a timeout kills its helper processes too;
    otherwise they keep stdout open and the call can hang forever."""
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
    cmd = [os.environ.get("CLAUDE_BIN", "claude"), "-p", "--system-prompt", system, "--tools", "", "--strict-mcp-config", "--setting-sources", "", "--no-session-persistence", "--output-format", "json", "--model", model]
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, start_new_session=True)
    except FileNotFoundError:
        raise LLMError("cli_missing", f"'{cmd[0]}' not found on PATH. Install Claude Code or set CLAUDE_BIN.")
    try:
        out, err = proc.communicate(user, timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        raise LLMError("timeout", f"Claude did not answer within {timeout}s.")
    if proc.returncode != 0:
        raise LLMError("cli_error", (err or out).strip()[:500] or f"exit {proc.returncode}")
    try:
        d = json.loads(out)
    except json.JSONDecodeError:
        raise LLMError("bad_json", f"Unparseable CLI output: {out[:200]!r}")
    if d.get("is_error"):
        raise LLMError("model_error", str(d.get("result"))[:500])
    return {"text": d["result"].strip(), "usage": d.get("usage", {}), "duration_ms": d.get("duration_ms")}


# ---------------------------------------------------------------- cache + ask
def _cache_path() -> Path:
    return CACHE_DIR / "qa_cache.json"


def _cache_key(q: str, size: int, k: int) -> str:
    norm = re.sub(r"\s+", " ", q.strip().lower()).rstrip("?. ")
    return hashlib.sha256(f"{norm}|{size}|{k}".encode()).hexdigest()[:16]


def cache_load() -> dict:
    return json.loads(_cache_path().read_text()) if _cache_path().exists() else {}


@dataclass
class Result:
    answer: str
    hits: list[tuple[dict, float]]
    cached: bool
    latency_ms: int
    usage: dict = field(default_factory=dict)


_retrievers: dict[int, Retriever] = {}


def ask(question: str, size: int = DEFAULT_SIZE, k: int = DEFAULT_K, bypass: bool = False) -> Result:
    """The one entry point used by the UI and the eval."""
    t0 = time.perf_counter()
    cache, key = cache_load(), _cache_key(question, size, k)
    if size not in _retrievers:
        _retrievers[size] = Retriever(size)
    r = _retrievers[size]
    if not bypass and key in cache:
        e = cache[key]
        by_id = {c["id"]: c for c in r.chunks}
        hits = [(by_id[i], s) for i, s in e["hits"] if i in by_id]
        return Result(e["answer"], hits, True, int((time.perf_counter() - t0) * 1000), e.get("usage", {}))
    hits = r.search(question, k)
    try:
        out = ask_claude(*build_prompt(question, hits))
    except LLMError as e:
        if e.kind not in ("timeout", "cli_error"):
            raise
        out = ask_claude(*build_prompt(question, hits))  # one retry for a stalled or crashed CLI
    ms = int((time.perf_counter() - t0) * 1000)
    cache[key] = {"question": question, "answer": out["text"], "hits": [(c["id"], s) for c, s in hits], "latency_ms": ms, "usage": out["usage"], "ts": time.time()}
    _cache_path().write_text(json.dumps(cache, indent=1))
    return Result(out["text"], hits, False, ms, out["usage"])


# ---------------------------------------------------------------- eval
_CITE = re.compile(r"\[(?:(\d+),\s*)?(?:([A-Za-z]+),\s*)?p\.?\s*(\d+)(?:\s*[-–]\s*(\d+))?\]")


def _parse_cites(answer: str, hits) -> list[tuple[str, str, str]]:
    """(doc, page_start, page_end) per citation; an index-only citation like [2, p.4] resolves doc via the source list."""
    out = []
    for idx, doc, p1, p2 in _CITE.findall(answer):
        if not doc and idx and 0 < int(idx) <= len(hits):
            doc = hits[int(idx) - 1][0]["doc"]
        out.append((doc or "?", p1, p2))
    return out


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s.lower())


def _figure_in(figs, text: str) -> bool:
    figs = [figs] if isinstance(figs, str) else figs
    return any(_norm(f) in _norm(text) for f in figs)


def _pct(vals, p):
    vals = sorted(vals); return vals[min(len(vals) - 1, int(round(p * (len(vals) - 1))))] if vals else 0


def run_eval(size: int, sizes: list[int] | None, no_llm: bool, failures: bool, k: int) -> None:
    qs = json.loads((ROOT / "eval_questions.json").read_text())
    in_corpus = [q for q in qs if q["type"] != "notfound"]
    lines = [f"# Eval — chunk size {size}, k={k}\n"]
    # criterion 1: retrieval hit rate per size
    lines += ["## Criterion 1 — retrieval hit rate (expected doc in top-k)\n", "| size | chunks | hits | rate |", "|---|---|---|---|"]
    for s in sizes or [size]:
        r = Retriever(s)
        hits = sum(all(d in {c["doc"] for c, _ in r.search(q["question"], k)} for d in ([q["doc"]] if isinstance(q["doc"], str) else q["doc"])) for q in in_corpus)
        lines.append(f"| {s} | {len(r.chunks)} | {hits}/{len(in_corpus)} | {hits / len(in_corpus):.0%} |")
        print(lines[-1])
    if no_llm:
        (CACHE_DIR / f"eval_retrieval.md").write_text("\n".join(lines)); return
    # criteria 2-4, 6, 8: one cold + one cached run per question
    lines += ["\n## Criteria 2-4, 8 — answers (cold run, then cached)\n", "| # | question | expected doc | top-k docs | hit | figure | cites ok | fig in cited | not-found ok | cold ms | cached ms | Correct | Grounded | Complete | note |", "|" + "---|" * 15]
    cold, cached_ms, rows = [], [], []
    for q in qs:
        res = ask(q["question"], size, k, bypass=True)
        res2 = ask(q["question"], size, k)
        cold.append(res.latency_ms); cached_ms.append(res2.latency_ms)
        docs = [c["doc"] for c, _ in res.hits]
        exp = [q["doc"]] if isinstance(q["doc"], str) else (q["doc"] or [])
        hit = all(d in docs for d in exp) if exp else "-"
        cites = _parse_cites(res.answer, res.hits)
        cite_ok = all(any(c["doc"] == d and c["page_start"] <= int(p1) and int(p2 or p1) <= c["page_end"] for c, _ in res.hits) for d, p1, p2 in cites) if cites else False
        fig_ok = _figure_in(q["figure"], res.answer) if q.get("figure") else "-"
        fig_cited = any(_figure_in(q["figure"], c["text"]) for c, _ in res.hits if any(c["doc"] == d for d, _, _ in cites)) if q.get("figure") and cites else "-"
        nf_ok = (res.answer.startswith(NOT_FOUND) and not re.search(r"\d+(\.\d+)?%", res.answer.split("\n")[0])) if q["type"] == "notfound" else "-"
        cache_ok = res2.cached and res2.answer == res.answer and res2.latency_ms < 100
        rows.append(f"| {q['id']} | {q['question']} | {','.join(exp) or '(none)'} | {','.join(dict.fromkeys(docs))} | {hit} | {fig_ok} | {cite_ok} | {fig_cited} | {nf_ok} | {res.latency_ms} | {res2.latency_ms}{'' if cache_ok else ' ✗'} |  |  |  |  |")
        lines.append(rows[-1]); print(f"{q['id']}: hit={hit} fig={fig_ok} cites={cite_ok} nf={nf_ok} cold={res.latency_ms}ms cached={res2.latency_ms}ms")
        lines.append(f"|  | **answer** | {res.answer.replace(chr(10), ' ').replace('|', '/')} |" + " |" * 12)
    # criterion 5: consistency, 3 questions x 3 bypassed runs
    lines += ["\n## Criterion 5 — consistency (3 single-doc questions × 3 cold runs)\n", "| # | run 1 | run 2 | run 3 | figure each run | identical figure |", "|---|---|---|---|---|---|"]
    for q in [x for x in qs if x["type"] == "single"][:3]:
        runs = [ask(q["question"], size, k, bypass=True) for _ in range(3)]
        cold += [r.latency_ms for r in runs]
        figs = [_figure_in(q["figure"], r.answer) for r in runs]
        lines.append(f"| {q['id']} | {runs[0].latency_ms} ms | {runs[1].latency_ms} ms | {runs[2].latency_ms} ms | {figs} | {all(figs)} |")
        print(f"consistency {q['id']}: {figs}")
    # criterion 6: latency
    lines += ["\n## Criterion 6 — latency\n", "| mode | n | p50 ms | p95 ms |", "|---|---|---|---|", f"| cold (retrieval + Claude) | {len(cold)} | {_pct(cold, .5)} | {_pct(cold, .95)} |", f"| cached | {len(cached_ms)} | {_pct(cached_ms, .5)} | {_pct(cached_ms, .95)} |"]
    print(lines[-2]); print(lines[-1])
    # criterion 7: failure handling
    if failures:
        lines += ["\n## Criterion 7 — failure handling\n", "| case | raised | message |", "|---|---|---|"]
        sysm, user = build_prompt("test", [])
        cases = [("cli_missing", dict(env_bin="/nonexistent/claude")), ("timeout", dict(timeout=0.01)), ("bad_json", dict(env_bin=str(_fake_cli())))]
        for kind, opts in cases:
            old = os.environ.get("CLAUDE_BIN")
            if "env_bin" in opts: os.environ["CLAUDE_BIN"] = opts["env_bin"]
            try:
                ask_claude(sysm, user, timeout=opts.get("timeout", 120)); got, msg = "none", "no error raised"
            except LLMError as e:
                got, msg = e.kind, str(e)
            finally:
                os.environ.pop("CLAUDE_BIN", None) if old is None else os.environ.__setitem__("CLAUDE_BIN", old)
            lines.append(f"| {kind} | {got} | {msg[:80].replace('|', '/')} |"); print(lines[-1])
    (CACHE_DIR / f"eval_{size}.md").write_text("\n".join(lines))
    print(f"\nwrote cache/eval_{size}.md")


def _fake_cli() -> Path:
    p = CACHE_DIR / "fake_claude.sh"
    p.write_text("#!/bin/sh\ncat >/dev/null\necho 'not json'\n"); p.chmod(0o755)
    return p


# ---------------------------------------------------------------- CLI
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", action="store_true", help="extract, clean, chunk and embed for --sizes")
    ap.add_argument("--sizes", default=None, help="comma list, e.g. 256,400,512,1024")
    ap.add_argument("--size", type=int, default=DEFAULT_SIZE, help="chunk variant to query/eval")
    ap.add_argument("--k", type=int, default=DEFAULT_K)
    ap.add_argument("--dump", metavar="DOC", help="print cleaned pages of one doc (short name)")
    ap.add_argument("--query", metavar="Q", help="retrieval-only smoke test, prints top-k")
    ap.add_argument("--ask", metavar="Q", help="full RAG answer in the terminal")
    ap.add_argument("--bypass", action="store_true", help="skip the answer cache")
    ap.add_argument("--eval", action="store_true", help="run eval_questions.json, write cache/eval_<size>.md")
    ap.add_argument("--no-llm", action="store_true", help="eval retrieval only (criterion 1)")
    ap.add_argument("--failures", action="store_true", help="include criterion 7 failure injection in --eval")
    a = ap.parse_args()
    sizes = [int(s) for s in a.sizes.split(",")] if a.sizes else None
    if a.dump:
        for doc, pdf, pages in load_docs():
            if doc == a.dump:
                for i, p in enumerate(pages, 1):
                    print(f"\n===== {doc} p.{i} {'(dropped)' if not p else ''} =====\n{p}")
    if a.build:
        build(sizes or [DEFAULT_SIZE])
    if a.query:
        for c, s in Retriever(a.size).search(a.query, a.k):
            print(f"\n[{s:.3f}] {c['id']} {c['doc']} p.{c['page_start']}-{c['page_end']} ({c['n_tokens']} tok)\n{c['text'][:400]}")
    if a.ask:
        res = ask(a.ask, a.size, a.k, a.bypass)
        print(f"\n{res.answer}\n\n-- {'cached' if res.cached else 'live'} · {res.latency_ms} ms · sources:")
        for i, (c, s) in enumerate(res.hits, 1):
            print(f"  [{i}] {c['doc']} p.{c['page_start']}-{c['page_end']} score {s:.3f}")
    if a.eval:
        run_eval(a.size, sizes, a.no_llm, a.failures, a.k)


if __name__ == "__main__":
    main()
