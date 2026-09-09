# QA System — RAG question answering over six PDF reports

A retrieval-augmented question-answering app. It reads the six PDFs in `data/`, cleans and splits
them into chunks, embeds the chunks locally, retrieves the most relevant ones for a question, and
prompts Claude to answer **only from those chunks** with page-level citations.

| Piece | Choice | Why |
| --- | --- | --- |
| Text extraction | `pdftotext` (poppler); `tesseract` OCR for the two scanned PDFs | Two files have no usable text layer |
| Embeddings | `BAAI/bge-small-en-v1.5` via sentence-transformers, runs on the laptop | Free, local, 384-dim, strong on short factual passages |
| Retrieval | Dense cosine + BM25 keyword search, fused by reciprocal rank | Dense alone missed one number-heavy question (see Reliability) |
| LLM | Claude (`sonnet`) through the Claude Code CLI in headless mode, tools disabled | No API key needed; the prompt is the whole interface |
| UI | Streamlit, one page | Question in, cited answer out, retrieved chunks inspectable |
| Code | `rag.py` (pipeline + CLI + eval), `app.py` (UI), `eval_questions.json` | Two files of code |

## How to run

Prerequisites: Python 3.12 with [uv](https://docs.astral.sh/uv/), poppler (`brew install poppler`),
tesseract (`brew install tesseract`), and the Claude Code CLI (`claude`) logged in.

```bash
uv sync                                                  # installs sentence-transformers, numpy, streamlit, rank-bm25
uv run python rag.py --build --sizes 256,400,512,1024    # extract, clean, chunk, embed (~3 min first time)
uv run python rag.py --ask "How many developers responded to the Sonar survey?"
uv run streamlit run app.py                              # the UI
uv run python rag.py --eval --no-llm --sizes 256,400,512,1024   # retrieval hit rate per chunk size
uv run python rag.py --eval --failures                   # full reliability run -> cache/eval_400.md
```

`cache/` holds the generated output: extracted pages, `chunks_<size>.json`, `emb_<size>.npy`,
`qa_cache.json` (every answer given), `eval_retrieval.md` and `eval_400.md` (the test results).

## The documents

| Short name | File | Pages | Words | Extraction | Known issues |
| --- | --- | --- | --- | --- | --- |
| METR | MEASUR~1.PDF — *Measuring the Impact of Early-2025 AI on Experienced Open-Source Developer Productivity* (arXiv 2507.09089) | 51 | 22.9k | pdftotext | Academic paper; 4 reference pages removed; tables lose their columns |
| Sonar | state-of-code-developer-survey-report.pdf | 57 | 11.5k | pdftotext | Chart-heavy; 5-line header/footer on every page; last page is another report's cover |
| ClaudeCode | How-Anthropic-teams-use-Claude-Code_v2pdf.pdf | 23 | 5.3k | **OCR** | Two overlapping text layers double every word under pdftotext, so it is OCR'd |
| OpenAI | the-state-of-enterprise-ai_2025-report.pdf | 24 | 5.0k | pdftotext | Footer and running heads on every page; scatter chart labels become number runs |
| Pragmatic | AI Impact on Software Engineers 2026.pdf (Pragmatic Engineer) | 20 | 3.8k | **OCR** | No text layer. **Paywalled: the PDF stops inside section 5 of 7.** Table columns interleave under OCR |
| Econ | Economics of AI Jobs - April 2026.pdf (Anthropic) | 15 | 3.2k | pdftotext | Cleanest file; running header lands mid-sentence; footnote digits fuse to words |

Total ≈ 190 pages, ≈ 51.6k words, ≈ 66k tokens after cleaning.

Cleaning is one generic routine (`clean_pages` in `rag.py`), no per-document rules: drop short lines
that recur on ≥30% of a document's pages (headers/footers), drop lines that are mostly digits (chart
axes), drop pages with fewer than 40 real words (covers, tables of contents), drop reference lists,
un-fuse footnote digits, join sentences that run across a page break.

## What chunk size is appropriate, given the size of the documents?

**Answer: about 400 tokens per chunk (hard cap 512), with a 50-token sentence overlap, splitting on
paragraph boundaries.**

**1. The corpus is small, so chunk size is a precision decision, not a cost decision.** After
cleaning, all six documents together are ≈66k tokens. Even at the smallest size tested the whole
index is a few hundred vectors, embedding takes seconds, and every search is a single matrix
multiply. Nothing about scale pushes toward large chunks.

| Target size | Hard cap | Chunks | Mean tokens | Median | p90 | Max |
| --- | --- | --- | --- | --- | --- | --- |
| 256 | 327 | 266 | 259 | 270 | 310 | 326 |
| **400** | **512** | **165** | **402** | **412** | **479** | **512** |
| 512 | 512 | 148 | 444 | 480 | 509 | 512 |
| 1024 | 1024 | 71 | 894 | 953 | 1014 | 1024 |

**2. The documents are made of short, statistic-dense claims.** Most facts in these reports are one
to three sentences: a chart headline plus its takeaway, a case-study paragraph, a survey finding.
The natural units (METR appendix factors, OpenAI case-study pages, Claude Code team spreads, Sonar
takeaway boxes) are 150–400 words, which is 200–500 tokens. A chunk should hold one claim together
with the sentence that interprets it, and not much else.

**3. Bigger chunks dilute the vector and feed the model distractor numbers.** At 1024 tokens a chunk
averages four to six unrelated statistics; the embedding becomes a blend and the LLM sees more
figures than it needs, which is how a "75%" from one paragraph ends up attached to the wrong claim.
1024 also exceeds the embedding model's 512-token window: everything past token 512 is silently
ignored when the chunk is embedded, so the retriever cannot even "see" the second half.

**4. Smaller chunks separate a figure from its meaning.** At 256 tokens, chart titles frequently
land in a different chunk from the explanatory sentence, and the METR paper's long paragraphs are
cut mid-argument. Retrieval still finds the document, but the model is handed fragments.

**5. Measured.** With hybrid retrieval all four sizes place the expected document in the top-5 for
11/11 in-corpus test questions (`cache/eval_retrieval.md`); dense-only retrieval at 400 was also
11/11 while 256, 512 and 1024 each missed one. 400 is the size at which the chunks match the
documents' natural units, stays inside the embedding window with margin, and gives the model roughly
five focused passages (~2k tokens of evidence) per question.

## Prompts

The assignment says "using prompts", so here is the entire prompt engineering. The LLM never sees
anything but this system prompt and the assembled user message.

**System prompt** (`SYSTEM_PROMPT` in `rag.py`):

```
You answer questions using ONLY the numbered sources provided in the user message.

Rules:
1. Every factual claim must end with a citation in the exact form [<doc>, p.<page>], copying <doc> and the page (or page range, e.g. p.8-10) exactly as written in the source header it came from. Example: [Sonar, p.14]. Never include the source number, e.g. not [1, Sonar, p.14] and not [1, p.14].
2. If the sources do not contain the answer, reply with exactly: Not found in the documents.
   Then add one sentence describing the closest related information in the sources, with a citation.
3. Do not use outside knowledge. Do not guess or round numbers; quote figures as they appear.
4. If two sources disagree, say so and cite both.
5. Keep the answer under 150 words unless the question asks to compare several documents.
6. Plain prose or short bullets. No preamble, no restating the question.
```

| Rule | What it buys |
| --- | --- |
| 1 | Every sentence is checkable: the citation names a chunk that is on screen in the UI. The eval verifies each citation resolves to a retrieved chunk. |
| 2 | A fixed refusal string makes "not found" machine-testable, and the follow-up sentence keeps the answer useful. |
| 3 | Stops the model from filling gaps with training-data knowledge (e.g. the population of France). |
| 4 | The METR study and the vendor surveys disagree; the model should surface that, not average it. |
| 5–6 | Screenshots stay readable; cross-document comparisons get room. |

**User message** — the top-k chunks with their headers, then the question:

```
Sources:

[1] Sonar, p.14-17
The personal account problem ... 35% of developers are accessing them through personal accounts ...

[2] Sonar, p.3-6
... The final sample size for the study included 1,149 responses ...

(… up to k sources …)

Question: What percentage of developers access AI coding tools through personal accounts?
```

The query itself is prefixed with bge's retrieval instruction
(`Represent this sentence for searching relevant passages:`) before embedding, which is the only
"prompt" the embedding model gets.

## Screenshots and how good the responses are

Six screenshots in `screenshots/`. Each is followed by a short quality verdict.

**`01_single_doc_live.png` — single-document fact, live call, source opened.**
Q: *What percentage of developers access AI coding tools through personal accounts rather than
work-sanctioned ones, according to the Sonar survey?* → "35% of developers access the top 10 AI
tools through personal accounts … [Sonar, p.14-17]", then a second sentence with the per-tool
breakdown (52% ChatGPT, 63% Perplexity, 17% Copilot) cited to p.17-18.
*Verdict:* Correct (35% is the figure on Sonar p.14). Grounded: source [1] is open under the answer
and contains the sentence. Complete, and slightly more than asked: the per-tool breakdown comes
from a chart page and the model attributed it correctly. Weak spot: the citation is a page range
because the chunk spans pages 14–17.

**`02_cached.png` — the same question asked again.**
Identical answer, badge reads `cached · 0 ms`. *Verdict:* Demonstrates the cache: no retrieval,
no LLM call, byte-identical text.

**`03_cross_doc.png` — cross-document comparison.**
Q: *Compare what the METR study measured about AI's effect on developer speed with what developers
in the Sonar survey report about AI's impact on their work.* → three paragraphs: METR's measured 19%
slowdown [METR, p.1-2]; Sonar's self-reported gains (39% and 34% productivity lifts, quality and
defect improvements) [Sonar, p.43-45, p.52-53]; then the methodological point that one measures
time-on-task and the other perception.
*Verdict:* Correct on both sides. Grounded: every paragraph cites, and the sources list shows 3 METR
and 2 Sonar chunks. Complete: this is the best answer in the set, it does the comparison rather
than listing two facts. Weak spot: Sonar pp.52–53 is a vendor section about SonarQube users, which
the answer does not flag.

**`04_not_found.png` — off-corpus question.**
Q: *What is the population of France?* → "Not found in the documents." followed by the closest
related fact (France's 143% growth in business customers [OpenAI, p.10-12]).
*Verdict:* Correct refusal, no invented number, and the follow-up sentence is genuinely the closest
thing in the corpus. This is rule 2 and rule 3 of the prompt working.

**`05_paywall_not_found.png` — question about the missing part of the truncated document.**
Q: *What percentage of Pragmatic Engineer survey respondents said they were less negative about AI
tools than in 2024?* → "Not found in the documents." plus the note that the intro says "fewer devs
are negative" without a figure [Pragmatic, p.1-2].
*Verdict:* Correct: the figure lives in section 6, which is behind the paywall and not in the PDF.
Grounded: the fallback sentence quotes the one place the topic is mentioned. This is the test that
the system knows what it does not know.

**`06_error_state.png` — the LLM is unavailable.**
App started with `CLAUDE_BIN=/nonexistent/claude`. The banner reads
`LLM call failed — cli_missing: '/nonexistent/claude' not found on PATH …` and the five retrieved
Econ chunks are still listed. *Verdict:* Failure is loud and specific, and retrieval still gives
the user something to read.

**Overall: how good are the responses?** On the 14 test questions, every in-corpus answer contained
the expected figure, every citation pointed at a retrieved chunk whose page range contains the
cited page, and all three questions that should be refused were refused with the exact phrase. The
answers are terse, quote figures verbatim, and do not editorialise. The weaknesses are inherited
from the documents, not the pipeline: OCR'd tables are unreadable, chart pages retrieve as lists of
labels, and the Pragmatic Engineer article is incomplete.

## Reliability: does it actually work?

All numbers come from `uv run python rag.py --eval --failures` (results in `cache/eval_400.md`,
chunk size 400, k = 5) and `--eval --no-llm` (`cache/eval_retrieval.md`). The 14 test questions
are in `eval_questions.json`: 10 single-document questions with a known figure, 1 cross-document
comparison, and 3 that must be refused (one about the paywalled part of the Pragmatic Engineer
article, two off-corpus).

| # | Criterion | How it is measured | Target | Result |
| --- | --- | --- | --- | --- |
| 1 | Retrieval hit rate | expected document in top-5 for each in-corpus question, per chunk size | ≥ 10/11 | **11/11** at 256, 400, 512 and 1024 (hybrid); dense-only: 400 = 11/11, others 10/11 |
| 2 | Answer correctness | expected figure string appears in the answer | ≥ 10/11 | **11/11** |
| 3 | Citation validity | every `[doc, p.N]` resolves to a retrieved chunk whose page range contains N | 100% | **14/14 answers, 100%** (after one prompt fix: the model had been writing `[1, Sonar, p.14]`) |
| 4 | Not-found behaviour | paywall question + 2 off-corpus questions return the literal phrase and no percentage | 3/3 | **3/3** |
| 5 | Consistency | 3 questions × 3 fresh (uncached) runs each contain the expected figure | 9/9 | **9/9** |
| 6 | Latency | p50 / p95 over 23 cold runs; p50 / p95 over 14 cached runs | cold p95 < 15 s; cached < 100 ms | cold **p50 4.4 s, p95 20.8 s**; cached **0 ms** (see note) |
| 7 | Failure handling | missing CLI, timeout, malformed CLI output each raise a distinct typed error; UI shows it | 3/3 + screenshot | **3/3**, `06_error_state.png` |
| 8 | Cache | same question twice: second is `cached=True`, identical text, < 100 ms | pass | **pass, 14/14** |

Latency note: the cold p95 misses the 15 s target because of two calls: the cross-document
comparison (20.8 s, a 600-token answer) and one call that stalled on the network, hit the 90 s
timeout, and was answered by the automatic retry (94.9 s total, counted). Excluding the retried
call, cold answers ranged 2.8–20.8 s with a median of 4.4 s. Nothing in the pipeline other than the
model call takes more than ~50 ms: retrieval over 165 chunks is a single matrix multiply plus a BM25
scan.

Dependability fixes found by the eval, in order: (1) the reference-list cleaner was swallowing 39
of METR's 51 pages, caught by the page-count check; (2) dense retrieval ranked the "more than 900
responses" chunk 6th, caught by criterion 2, fixed with BM25 fusion; (3) the model copied the
source index into citations, caught by criterion 3, fixed in the prompt; (4) a stalled CLI call
hung indefinitely because Python's subprocess timeout could not reap the CLI's helper processes,
caught by wall-clock vs. measured latency, fixed by running the CLI in its own process group and
killing the group.

Per-question results, with the rubric columns (Correct / Grounded / Complete), are in
`cache/eval_400.md`.

## Limitations

- **The Pragmatic Engineer PDF is truncated at the paywall.** Sections 5–7 are missing. The system
  correctly answers "Not found" for them, but a reader who does not know the gap could wonder why.
- **Tables and charts.** OCR interleaves table columns, and pdftotext turns charts into runs of
  numbers. The cleaner drops most of the noise; some chart labels survive as harmless residue.
- **Latency floor.** Each answer goes through the Claude Code CLI, which adds ~1–2 s of process
  start-up on top of the model call. Typical cold answers take 3–4 s; the cache returns repeats in
  under a millisecond. Occasional network stalls are handled by a 90 s timeout and one retry.
- **Runs on the author's Claude subscription.** The grader can read the code and outputs without
  running anything; to run it you need the Claude Code CLI logged in (or swap `ask_claude` for a
  direct API call, which is a one-function change).
- **Four of the six documents are vendor-authored** (Sonar, OpenAI, Anthropic ×2). The system reports
  what they say with citations; it does not judge them.
