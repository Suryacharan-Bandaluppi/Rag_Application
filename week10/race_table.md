# race_table.md — single agent vs orchestrator, same 10 cases, same judge

**Cases (unchanged):** CLM-001, CLM-002, CLM-003, CLM-004, CLM-005, CLM-006, CLM-007, CLM-008, CLM-009, CLM-010 — the 10 claim cases in `week7/claims.jsonl` that the single agent was built and evaluated on (Week 7 race, Week 8 trajectory eval).  
**Judge (unchanged, imported):** `week8/trajectory_eval.outcome_pass`. A case passes if the verdict matches AND the payable amount is within £1.  
**Model:** `openai/gpt-oss-120b`, temperature 0, one run per case per arm, arms interleaved. Latency excludes Groq 429 back-off. Cost uses $0.90 per 1M tokens (week7 constants).

> Why not `week6/eval_cases.jsonl`? In this repo Week 6 evaluated the PaySlate **developer-docs RAG** app (25 questions such as *"minimum Dart SDK version"*). It contains no insurance claims, so a claims agent cannot be run on it. The claims eval set that already exists, and that the single agent was scored on, is the 10 cases above. No case was added, removed or edited.

| Metric | Single agent | Orchestrator | Δ |
|---|---|---|---|
| **Pass rate** | 10/10 = 100% | 10/10 = 100% | +0 pp |
| **Latency p50** | 3.54 s | 7.77 s | 2.2x |
| **Latency p99** | 6.22 s | 10.07 s | 1.6x |
| **Total tokens** (10 cases) | 48,305 | 74,879 | 1.6x |
| **Cost per claim** (mean) | $0.004347 | $0.006739 | 1.6x |

Supporting numbers:

| | Single agent | Orchestrator |
|---|---|---|
| Cost per claim p50 / max | $0.004366 / $0.004664 | $0.006716 / $0.007260 |
| Total cost, 10 claims | $0.043475 | $0.067391 |
| LLM calls per claim (mean) | 4.0 | 8.1 |
| Slowest case (= p99 with n=10, nearest rank) | 6.22 s (CLM-006) | 10.07 s (CLM-006) |

p99 over 10 runs is the nearest-rank 99th percentile, which is the slowest run; it is reported, not hidden.

## Per-case results

| Case | Expected | Single | Orchestrator |
|---|---|---|---|
| CLM-001 | denied £0 | ✅ denied £0 · 4,533 tok · 3.3s | ✅ denied £0 · 7,302 tok · 7.7s |
| CLM-002 | denied £0 | ✅ denied £0 · 4,839 tok · 3.1s | ✅ denied £0 · 8,067 tok · 7.7s |
| CLM-003 | denied £0 | ✅ denied £0 · 4,565 tok · 3.2s | ✅ denied £0 · 7,050 tok · 7.9s |
| CLM-004 | approved £3950 | ✅ approved £3950 · 4,906 tok · 4.7s | ✅ approved £3950 · 7,285 tok · 7.1s |
| CLM-005 | approved £6300 | ✅ approved £6300 · 4,863 tok · 3.6s | ✅ approved £6300 · 7,560 tok · 7.2s |
| CLM-006 | approved £1600 | ✅ approved £1600 · 4,930 tok · 6.2s | ✅ approved £1600 · 7,696 tok · 10.1s |
| CLM-007 | partial £7600 | ✅ partial £7600 · 4,782 tok · 4.7s | ✅ partial £7600 · 7,510 tok · 7.8s |
| CLM-008 | approved £2850 | ✅ approved £2850 · 4,760 tok · 4.2s | ✅ approved £2850 · 7,593 tok · 8.3s |
| CLM-009 | partial £2050 | ✅ partial £2050 · 5,182 tok · 3.5s | ✅ partial £2050 · 7,414 tok · 7.0s |
| CLM-010 | approved £17750 | ✅ approved £17750 · 4,945 tok · 2.9s | ✅ approved £17750 · 7,402 tok · 8.5s |

## Multiplier

**Context re-send multiplier: 74,879 / 48,305 = 1.6x  —  largest share: "orchestrator -> exclusions worker" 17,918 tokens = 24% of all orchestrator-arm tokens**

Top hand-offs by token share (full breakdown in `handoffs.log`):

| Hand-off | Tokens | Share |
|---|---|---|
| orchestrator -> exclusions worker | 17,918 | 23.9% |
| orchestrator -> notes worker | 17,416 | 23.3% |
| exclusions worker -> orchestrator | 13,300 | 17.8% |
| notes worker -> orchestrator | 9,917 | 13.2% |
| get_claim -> orchestrator | 8,707 | 11.6% |

## Synthesis fidelity check

Cases where the exclusions worker returned payout_modifier 0 but the orchestrator did not deny: **0** .

