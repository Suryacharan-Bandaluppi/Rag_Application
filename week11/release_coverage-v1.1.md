# Release coverage-v1.1: prompt version bump

| | Before | After |
|---|---|---|
| `CURRENT_PROMPT_VERSION` (`week11/prompts.py`) | `coverage-v1.0` | **`coverage-v1.1`** |
| Retrieval pinned to that version (`RETRIEVAL_FOR`) | `bm25-k3-v1` | **`bm25-k3-v2-excl`** |
| Eval suite | 7/11 (RED) | **11/11 (GREEN, twice)** |
| Mean tokens / cost per query (suite) | 1,630 / $0.0014674 | 1,837 / $0.0016534 (+12.7%) |

**What changed and why.** Trace `tr_044698c89a65` paid a flood loss under the storm clause. Its context ids proved that retrieval returned the cover clause (4.2a) without the exclusion that limits it (4.2b), so this was a retrieval miss, not the model ignoring context. The fix is at the retrieval and prompt layer:
- **Exclusion pairing:** every cover clause in context brings the exclusions that modify it.
- **Claim lookups** retrieve on the claim's declared type and notes, not on the bare question.
- **Generic words** ("claim", "covered", "payable") no longer drive retrieval.
- **Prompt:** exclusions get their own section and must be checked against the facts before any "covered" answer.

Every logged trace carries `prompt_version`, so v1.0 traces stay replayable with `app.py ask --prompt-version coverage-v1.0`.

**Canary and rollback (two lines):**
1. **Canary:** serve coverage-v1.1 to adj-04 and adj-06 (10 of yesterday's 46 requests, 22%) for one business day, after a shadow replay of yesterday's 46 queries. Promote only if the suite stays 11/11, no `decision=covered` trace has an exclusion in `added_ids` that its answer ignores, and cost per query stays within +15% of v1.0.
2. **Rollback:** set `CURRENT_PROMPT_VERSION = "coverage-v1.0"` in `week11/prompts.py`. Because `RETRIEVAL_FOR` pins retrieval to the prompt version, this one-line revert restores both prompt and retrieval, and new traces immediately log `prompt_version=coverage-v1.0`.
