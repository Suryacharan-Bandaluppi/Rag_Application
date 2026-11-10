# Week 11 · Task Set D — Find the coverage answer that ignored an exclusion

**Complaint:** "it told a claimant something was covered when the policy excludes it, sometime yesterday." That is all.
**Outcome:** found from the logs in **00:17**. Root cause proven from the logged context ids: retrieval missed the flood exclusion. Turned into eval case C11, then the full suite went **7/11 RED → 11/11 GREEN** (confirmed twice) with release **coverage-v1.1**. At 10× volume, the **daily token rate limit** breaks first, at 3.5×.

## The app (built this week, `week11/`)

A claims **coverage assistant** for adjusters, on the same policy wording and claims data as Weeks 7–10:

```
question ─► retrieval (BM25 over policy clauses, local, $0) ─► tools (LLM turn that calls get_claim + the lookup) ─► generation (LLM turn that calls submit_answer)
```

| File | Role |
|---|---|
| `app.py` | The app. Every request is one trace written to `logs/traces.jsonl` |
| `observability.py` | Traces and spans (stage, latency, tokens, cost per span), **PII redacted on write**, log kept sorted by time |
| `prompts.py` | Versioned prompts. Each version is pinned to its retrieval config (`RETRIEVAL_FOR`) |
| `retrieval.py` | `bm25-k3-v1` (shipped) and `bm25-k3-v2-excl` (the fix) |
| `logq.py` | Log search used in the drill: slices for time, user, prompt version, input type and cost outlier, plus output fields (`decision`, answer text) and context ids |
| `evals/` | The suite: 10 existing claim cases (CLM-001…010, unchanged expectations from `week7/claims.jsonl`) + **C11** from the drill; `run_suite.py` |
| `cost_report.py`, `capacity.py` | Cost per stage; what breaks at 10× |

**What each trace logs:** trace_id, ts, user_id, input_type, prompt_version, retrieval_version, model, claim_number, `query_redacted`, `answer_redacted`, `decision`, payable, clauses_cited, `context_ids`, and per-span latency, tokens and cost.

**Never logged:** adjuster note text, tool results, claimant names, emails, phones, postcodes, policy numbers. A check of the 46 production traces found 0 leaked names, emails or policy numbers.

## Deliverables

| Checklist item | File | Result |
|---|---|---|
| Time-to-find in mm:ss, slice used, named squadmate | `drill.md` | **00:17**; time slice + output index (`decision`) → 46 → 21 → 1 |
| The found trace | `trace.json` | Per-span latency, tokens, cost; `prompt_version` coverage-v1.0; `context_ids` 4.2a, 1.2, 1.1 (no 4.2b) |
| The eval case file | `evals/cases/C11_drill_storm_flood.json` | Question verbatim from the trace; expected `not_covered`, must cite 4.2b |
| Suite RED then GREEN with counts | `suite_red_green.md` (raw: `evals/results/`) | **7/11 → 11/11**, second run 11/11 |
| Prompt version bump, canary and rollback | `release_coverage-v1.1.md`, `prompts.py` changelog | coverage-v1.0 → **coverage-v1.1** (retrieval bm25-k3-v1 → bm25-k3-v2-excl) |
| Cost per query by stage | `cost_by_stage.md` | That request: retrieval $0, tools $0 (no claim number, so no tool call), generation $0.0007137 (100%). Day average: tools 19.5%, generation 80.5% |
| What breaks first at 10× | `tenx.md` | **Tokens per day: 567,690 vs 200,000 TPD, hit at 3.5×** |

## How the drill was kept honest
- **Blind planting.** A **subagent "squadmate"** generated yesterday's 46 production requests and planted one bad answer. I saw only its one-line "Planted" message, and its answer key stayed sealed until after the find (now in `evidence/answer_key.json`).
- **Real failure, not an edited log line.** The planted answer was produced by the app itself; the squadmate reproduced it 4/4 in a scratch log first. That is why the eval case can genuinely go RED.
- **Order of operations, as the brief requires:**
  1. Find the trace.
  2. Write C11.
  3. Run the **full** suite on the **unchanged** v1.0 → RED 7/11.
  4. Fix.
  5. Run the full suite → GREEN 11/11, then again → 11/11.

## Findings beyond the planted needle
- **The suite was already red before the drill.** The v1.0 baseline was **8/10** (`evals/results/baseline_v1.0_10cases.txt`): the flood claim (C01) and the drink-driving claim (C03) failed, sometimes answered "covered" with a payout, because claim lookups retrieved the same generic clauses (`1.1, 1.2, 8.1`) for every claim.
- **The fix had to avoid a regression trap.** In the RED run, C10 (a vehicle **theft**) was denied under 8.1 "criminal acts", pulled in only because 8.1 contains the word "claim". Pinning 8.1 into every prompt would have made this worse. The suite caught it, and v1.1 passes it.
- **Price of the fix:** +12.7% tokens and cost per query (1,630 → 1,837 tokens), measured on the suite.

## Changes made to the app before traffic (infrastructure only, not coverage logic)
These were all fixed **before** the squadmate wrote any traffic, except the connection retry and the clause-id cleanup, which were added after the first v1.1 suite attempt crashed on a dropped connection (case 6 of 11), before the first complete GREEN run.
- **Fake tool names:** gpt-oss sometimes calls tools that don't exist (`json`, `commentary`). The app now retries, then repairs, and logs `rejected_calls` / `repaired_from_tool` on the span. Groq reports no tokens for rejected calls; the span is flagged `untracked_tokens`.
- **Plain-text answers:** one format reminder to call `submit_answer`, logged as an `llm.format_retry` span.
- **Dropped connections / timeouts** are retried; cited clause ids are normalised (`"[7.4]"` → `"7.4"`).

## Deviations and limits (stated plainly)
- **"Week-6 suite".** In this repo Week 6 is the PaySlate developer-docs RAG suite, which has no claims. As in Week 10, the claims suite is the 10 existing claim cases plus the new case.
- **Timestamps.** Traffic was generated today and recorded with simulated 2026-10-26 timestamps (`--at`). Latencies and tokens are real.
- **The finder is an AI.** 00:17 reflects reading 21 rows at machine speed, plus having built the logger; it is not a human benchmark.
- **One limit is not measured directly.** TPD 200K comes from Groq's published table, not a response header (RPD and TPM are read from headers and match that table row).
- **Bonus not attempted.** It needs a second planting and a faster find than 00:17, and today's token budget (200K TPD) was largely spent on the drill and suite runs. The field that would make the find instant is named in `drill.md`.

## Re-run
```bash
python3 week11/logq.py --date 2026-10-26 --facets
python3 week11/logq.py --date 2026-10-26 --decision covered --full
python3 week11/evals/run_suite.py --label check          # current version (v1.1)
python3 week11/cost_report.py --trace tr_044698c89a65
python3 week11/capacity.py
```
