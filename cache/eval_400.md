# Eval — chunk size 400, k=5

## Criterion 1 — retrieval hit rate (expected doc in top-k)

| size | chunks | hits | rate |
|---|---|---|---|
| 400 | 165 | 11/11 | 100% |

## Criteria 2-4, 8 — answers (cold run, then cached)

| # | question | expected doc | top-k docs | hit | figure | cites ok | fig in cited | not-found ok | cold ms | cached ms | Correct | Grounded | Complete | note |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Q1 | How much did allowing AI tools change task completion time for experienced developers in the METR study? | METR | METR | True | True | True | True | - | 94900 | 0 | Y | Y | Y | Adds the forecast comparison unprompted; all figures verified on p.1 |
|  | **answer** | Allowing AI tools **increased** completion time by **19%**—meaning developers were actually slower when using AI tooling [METR, p.1]. This contradicted developers' pre-study forecast that AI would *reduce* completion time by 24%, their post-study estimate of a 20% reduction, and expert predictions of 38–39% speedup from ML and economics forecasters [METR, p.1]. | | | | | | | | | | | | |
| Q2 | What share of Cursor's AI generations did developers accept in the METR study? | METR | METR | True | True | True | True | - | 4763 | 0 | Y | Y | Y | Terse; "<44%" quoted as in source |
|  | **answer** | Less than 44% [METR, p.20]. | | | | | | | | | | | | |
| Q3 | What percentage of developers access AI coding tools through personal accounts rather than work-sanctioned ones, according to the Sonar survey? | Sonar | Sonar | True | True | True | True | - | 3917 | 0 | Y | Y | Y | Per-tool breakdown from chart page correctly attributed |
|  | **answer** | 35% of developers access the top 10 AI tools through personal accounts rather than work-sanctioned ones [Sonar, p.14-17]. This varies significantly by tool: 52% of ChatGPT users access it via personal accounts, 63% of Perplexity users do the same, while GitHub Copilot and Amazon Q Developer show much lower personal account use at only 17% each [Sonar, p.17-18]. | | | | | | | | | | | | |
| Q4 | How many developers responded to the Sonar State of Code survey, and when was the fieldwork? | Sonar | Sonar | True | True | True | True | - | 5545 | 0 | Y | Y | Y | Both parts answered |
|  | **answer** | The survey included **1,149 responses**, and fieldwork ran **throughout October 2025** [Sonar, p.3-6]. | | | | | | | | | | | | |
| Q5 | In OpenAI's enterprise report, how much did the median industry sector grow year over year? | OpenAI | OpenAI | True | True | True | True | - | 2866 | 0 | Y | Y | Y | Cites the executive-summary page rather than the chart page; both say 6x |
|  | **answer** | The median sector grew by more than 6x year-over-year [OpenAI, p.3-4]. | | | | | | | | | | | | |
| Q6 | How much has API use by non-technology firms grown year over year, according to OpenAI? | OpenAI | OpenAI | True | True | True | True | - | 3014 | 0 | Y | Y | Y | — |
|  | **answer** | Non-technology firm API use has grown **5x year-over-year** [OpenAI, p.10-12]. | | | | | | | | | | | | |
| Q7 | How did Anthropic's Data Infrastructure team use Claude Code to fix a Kubernetes cluster problem? | ClaudeCode | ClaudeCode | True | True | True | True | - | 5011 | 0 | Y | Y | Y | Full narrative; matches the case study |
|  | **answer** | When Kubernetes clusters went down and weren't scheduling new pods, the team fed screenshots of dashboards into Claude Code [ClaudeCode, p.1-3]. Claude Code guided them through Google Cloud's UI menu by menu until it identified a warning indicating pod IP address exhaustion [ClaudeCode, p.1-3]. It then provided the exact commands to create a new IP pool and add it to the cluster, resolving the issue without needing to involve networking specialists [ClaudeCode, p.1-3]. | | | | | | | | | | | | |
| Q8 | In the Claude Code playbook, roughly what share of one team's feature implementation came from Claude's autonomous work? | ClaudeCode | ClaudeCode | True | True | True | True | - | 4843 | 0 | Y | Y | Y | Names the Vim-mode example, which the question did not |
|  | **answer** | Roughly 70% of the final implementation came from Claude's autonomous work, specifically when building the Vim mode feature for Claude Code [ClaudeCode, p.4-5]. | | | | | | | | | | | | |
| Q9 | How many Claude users were surveyed for Anthropic's Economics of AI Jobs report? | Econ | Econ | True | True | True | True | - | 2782 | 0 | Y | Y | Y | 81,000 (headline) not 80,508 (acknowledgements); both in corpus |
|  | **answer** | 81,000 Claude users were surveyed [Econ, p.2]. | | | | | | | | | | | | |
| Q10 | How many responses did the Pragmatic Engineer AI tooling survey receive? | Pragmatic | Pragmatic,METR | True | True | True | True | - | 3573 | 0 | Y | Y | Y | Retrieved only after BM25 fusion was added |
|  | **answer** | More than 900 responses [Pragmatic, p.1-2]. | | | | | | | | | | | | |
| Q11 | Compare what the METR study measured about AI's effect on developer speed with what developers in the Sonar survey report about AI's impact on their work. | METR,Sonar | METR,Sonar | True | True | True | True | - | 20778 | 0 | Y | Y | Y | Best answer in the set; does not flag Sonar pp.52-53 as vendor material |
|  | **answer** | The two sources directly contradict each other. METR's controlled field experiment found that tasks took **19% more time** when developers used AI tools like Cursor Pro—a slowdown effect described as robust across analyses [METR, p.1-2]. Experts and participants also substantially *overestimated* how much AI would help [METR, p.1-2].  By contrast, the Sonar survey of 1,149 professionals found self-reported productivity *gains*: SMB developers reported a **39% increase** in personal productivity, while other developers reported a **34% lift** [Sonar, p.43-45]. Sonar respondents also reported positive impacts on code quality, lower defect rates, and faster time-to-market [Sonar, p.52-53].  The key methodological difference: METR measures *actual task completion times* in real open-source work, while Sonar captures *self-perceived* productivity. The gap likely reflects that developers' subjective sense of being faster diverges significantly from measured outcomes—precisely the inferential problem METR set out to address [METR, p.1-2]. | | | | | | | | | | | | |
| Q12 | What percentage of Pragmatic Engineer survey respondents said they were less negative about AI tools than in 2024? | (none) | Pragmatic,Econ,Sonar | - | - | True | - | True | 4374 | 0 | Y | Y | Y | Correct refusal for the paywalled section |
|  | **answer** | Not found in the documents.  The closest related information is that the Pragmatic Engineer survey notes "Fewer devs are negative about Al" compared to 2024, but no specific percentage is provided [Pragmatic, p.1-2]. | | | | | | | | | | | | |
| Q13 | What did the 2025 Stack Overflow Developer Survey find about Rust adoption? | (none) | METR | - | - | True | - | True | 4369 | 0 | Y | Y | Y | Correct refusal; fallback fact is loosely related |
|  | **answer** | Not found in the documents.  The closest related information concerns developer tool usage patterns, specifically rates of prior usage of tools like Visual Studio Code (63%), GitHub Copilot (56%), and Cursor (25%) among study participants [METR, p.50-51]. | | | | | | | | | | | | |
| Q14 | What is the population of France? | (none) | OpenAI,Econ,Sonar | - | - | True | - | True | 3133 | 0 | Y | Y | Y | Correct refusal; fallback fact is the France growth stat |
|  | **answer** | Not found in the documents.  The closest related information mentions France as one of the fastest-growing markets for business customers, increasing more than 143% year-over-year [OpenAI, p.10-12]. | | | | | | | | | | | | |

## Criterion 5 — consistency (3 single-doc questions × 3 cold runs)

| # | run 1 | run 2 | run 3 | figure each run | identical figure |
|---|---|---|---|---|---|
| Q1 | 4984 ms | 5284 ms | 5745 ms | [True, True, True] | True |
| Q2 | 3224 ms | 2632 ms | 2597 ms | [True, True, True] | True |
| Q3 | 3356 ms | 3975 ms | 6438 ms | [True, True, True] | True |

## Criterion 6 — latency

| mode | n | p50 ms | p95 ms |
|---|---|---|---|
| cold (retrieval + Claude) | 23 | 4369 | 20778 |
| cached | 14 | 0 | 0 |

## Criterion 7 — failure handling

| case | raised | message |
|---|---|---|
| cli_missing | cli_missing | cli_missing: '/nonexistent/claude' not found on PATH. Install Claude Code or set |
| timeout | timeout | timeout: Claude did not answer within 0.01s. |
| bad_json | bad_json | bad_json: Unparseable CLI output: 'not json\n' |