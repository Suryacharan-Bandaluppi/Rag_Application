# drill.md — find the coverage answer that ignored an exclusion

**Complaint given:** "an adjuster says it told a claimant something was covered when the policy excludes it, sometime yesterday." No trace id, claim number or time.

**Time-to-find: 00:17** (mm:ss). Clock start was 2026-10-27T09:03:28Z, the first log query. Clock stop was 09:03:45Z, when the trace was identified. Both stamps are in `evidence/drill_clock.txt`.
**Found:** `tr_044698c89a65`, 2026-10-26T14:41:17Z, user adj-04. The sealed answer key confirms it (`evidence/answer_key.json`).
**Slice that found it:** the **time** slice (`--date 2026-10-26`) plus the **output index** (`--decision covered|partially_covered`), which cut 46 traces to 21. I then read those 21 redacted answers against the policy wording. None of the brief's five named slices finds it on its own (see below).

## Who planted it, who timed it
- **Planted by "squadmate", a Claude subagent run in this session.** It wrote 46 production traces for 2026-10-26 and planted one real wrong answer, produced by the app itself (4/4 wrong in scratch). It sealed the answer key, and its only message back was "Planted. Production log has 46 traces for 2026-10-26."
- **Found by the main session**, using only `week11/logq.py` against `week11/logs/traces.jsonl`. The key was opened only after the clock stopped.
- **Timed by wall-clock stamps** written in the same session (`date -u` before the first query and after the find).

## The search, step by step
| mm:ss | Query | Result |
|---|---|---|
| 00:00 | `logq.py --date 2026-10-26 --facets` | 46 traces. prompt_version: 46× v1.0. decision: covered 17, partially_covered 4, not_covered 17, cannot_determine 8 |
| ~00:05 | `logq.py --date 2026-10-26 --decision covered --full` (and `partially_covered`) | 21 covered-type answers with redacted Q and A, context ids and cited clauses |
| 00:17 | Read against the wording | One answer pays a loss the policy excludes: *"street gullies couldnt cope and rainwater came in under the front door … Storm claim"*, answered **covered £8,650 under 4.2a**. Rain through an undamaged door from overflowing drains / surface run-off is **flood (4.2b), excluded**. Its context ids `4.2a, 1.2, 1.1` show 4.2b was never retrieved. |

## Which slices would have found it

| Slice | Would it isolate the trace? |
|---|---|
| time | Necessary but not enough: 46 traces yesterday |
| user | No: adj-04 is one of 6 users and was unknown in advance |
| prompt version | No: all 46 traces are coverage-v1.0 |
| input type | No: it is one of 15 `excess_question` traces |
| cost outlier | No: $0.000714 is ordinary and not in the top 5 by cost |
| **output: `decision`** | **Yes, with reading: 46 → 21 candidates** |
| context ids | Diagnosed it in one hop: covered, with the cover clause (4.2a) present but no exclusion clause (4.2b) in context |

## Honest notes
- **Under 5 minutes**, so no missing field cost the time. One field would make it *instant* (a one-row query instead of reading 21 answers): a per-trace **`exclusions_in_context`** list next to `decision`. The query `decision=covered AND cover clause retrieved WITHOUT its paired exclusion` would then return this trace alone. v1.1 now logs `added_ids` on the retrieval span, which records exactly which exclusion clauses were attached and why.
- **The finder had advantages.** The finder is an AI that reads 21 rows in seconds, and it also built the logger, so 00:17 is not a human benchmark. A human reading the same 21 rows would plausibly take a few minutes. That is still under 5, because the output (`decision`, `answer_redacted`) is indexed.
- **This only worked because outputs are logged.** Searching the input alone ("flood", "exclusion") finds nothing: the question never says "flood", only "storm".
- **No PII was needed.** `query_redacted` and `answer_redacted` were redacted on write; claimant names, emails and phones in other traces appear as `[NAME]`, `[EMAIL]`, `[PHONE]`.
