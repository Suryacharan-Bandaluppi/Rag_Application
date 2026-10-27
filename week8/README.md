# Week 8 · Task Set D — Outcome-vs-trajectory gap in the claims agent

**System under test:** the Week 7 claims agent (`week7/agent.py`): same system prompt, the same three tools plus `submit_verdict`, the same four budgets, `openai/gpt-oss-120b` on Groq at temperature 0.
**Runs:** 10 claims × 3 repeats = 30 runs per variant (baseline, then mitigated).
**Full auto-generated tables:** [`results.md`](results.md). Raw traces: `traces_baseline.jsonl`, `traces_mitigated.jsonl`. Run logs: `run_baseline.log`, `run_mitigated.log`.

## Files

| File | What it is |
|---|---|
| `trajectory_eval.py` | The 10 expected sequences (`EXPECTED`), argument checks, failure-mode classifier, self-test, runner and report |
| `agent.py` | The Week 7 agent loop with tracing added: every tool call is recorded with its arguments and result |
| `mitigation.py` / `mitigation.diff` | The single mitigation (tighter `get_adjuster_notes` description) and its exact diff against week7 |
| `results.md` | Generated tables: expected paths, metrics, gap, trace, per-mode regression, price |

```bash
python3 week8/trajectory_eval.py selftest                       # no API calls
python3 week8/trajectory_eval.py run --variant baseline  --repeats 3
python3 week8/trajectory_eval.py run --variant mitigated --repeats 3
python3 week8/trajectory_eval.py report                         # writes results.md
```

---

## 1. Expected tool sequences: 10 cases, alternate paths asserted as a set

Each call is reduced to a signature: `claim`, `notes:<status>`, `excl:<cause>`, `submit`. Each case has a **set** of accepted sequences. A run passes the trajectory eval only if its exact sequence is in that set **and** every argument is valid.

| Claim | Kind | Needed | # accepted paths | Why more than one path is legitimate |
|---|---|---|---|---|
| CLM-001 | conditional (water_damage → flood) | 4 | 3 | claim/notes order is free; may look up the declared type first if it then reads the notes and looks up `flood` |
| **CLM-002** | conditional (fire → arson) | **5** | **6** | as above, plus the **escalated** notes are required (reviewed notes say arson is only "under consideration… senior review recommended"); they may be read before or after the arson lookup |
| CLM-003 | conditional (vehicle_accident → intoxicated_driving) | 4 | 3 | same as CLM-001 |
| CLM-004…010 | clean (cause = declared type) | 4 | 3 each | claim/notes order is free; the exclusion lookup may come before the notes, since the notes only confirm the cause |

All 10 cases accept more than one path. That is deliberate: reading the notes before or after pulling the claim are both correct, and asserting one exact order would score correct runs as failures and inflate the gap.

**Rejected on purpose:**
- never calling `search_policy_exclusions`
- looking up only the declared type on a conditional claim
- denying CLM-002 without the escalated confirmation
- looking up the actual cause before reading the notes (a guess)

`selftest` checks that all 33 accepted sequences pass, and that one synthetic bad path per failure mode is caught under the right mode.

## 2. Trajectory numbers (baseline)

| Metric | Baseline |
|---|---|
| Tool-choice accuracy | **97.6%** |
| Argument validity rate | **100.0%** (180/180: every claim id, status, cause and exclusion id was real and grounded) |
| Step efficiency (taken / needed) | **0.980** mean; p50 1.00, min **0.80**, max 1.00 |
| Cost per claim | **p50 $0.004360**, **max $0.004653** (mean $0.004376 for reference only) |
| Tokens per claim | p50 4,844, max 5,170 |

Step efficiency under 1.0 is **not** a good sign here. The 0.80 runs are CLM-002 taking 4 steps when 5 were needed: the agent skipped a required step. The agent never wasted a step (no run went above 1.0). Its failure is cutting corners.

Cost variance is narrow: max is 1.07× p50. No run looped, because the agent always made exactly 4 LLM calls.

## 3. The gap

**Outcome pass 100.0% − trajectory pass 90.0% = +10.0 percentage points.**

The outcome eval passes all 30 runs. 3 of those 30 reach the right payout down a wrong path.

**Right answer, wrong path: CLM-002 (fire claim, £48,000), 3 of 3 repeats.**

```
1. get_claim(CLM-002)                         -> declared_type=fire, £48,000
2. get_adjuster_notes(CLM-002, "reviewed")    -> "...arson inquiry opened... Criminal acts exclusion
                                                  (Clause 8.1) is under consideration pending
                                                  investigation outcome. Senior review recommended."
3. search_policy_exclusions("arson")          -> arson_and_criminal_acts, payout_modifier 0.0
4. submit_verdict(denied, £0, arson_and_criminal_acts)
```

- **Wrong path:** `claim > notes:reviewed > excl:arson > submit`. It is missing `notes:escalated`.
- **Mode:** `skipped_required_step`.

The agent denied a £48k claim on an arson inquiry that, by its own evidence, was still open. The confirmation ("Arson confirmed by FIU report dated 2024-04-09") exists only in the escalated notes, which the agent never opened. It passes because the FIU happened to confirm arson. Had the inquiry cleared the insured, the same path would have wrongly denied a valid claim, and the outcome eval would have had nothing to flag. This is the director's time bomb.

## 4. The one mitigation: tighter tool description

- **Top mode:** `skipped_required_step` (3/30 runs; the only mode that fired in the baseline).
- **Change:** only the `get_adjuster_notes` description and its `status` parameter description. They now say that a provisional finding in the reviewed notes ("under consideration", "senior review recommended"…) is unconfirmed, and that the agent must read `status='escalated'` before submitting.
- **Unchanged:** system prompt, tool set, enum, budgets. There is no argument validation, step limit or re-planning. See `mitigation.diff`.

| | Before | After |
|---|---|---|
| **`skipped_required_step`** | **3** | **0** |

The mitigation did what it targeted: all 3 CLM-002 runs now read the escalated notes (`claim > notes:reviewed > notes:escalated > excl:arson`).

**Price paid (measured):**

| | Baseline | Mitigated | Δ |
|---|---|---|---|
| Tokens / claim p50 | 4,844 | 5,190 | **+345 (+7.1%)** |
| Cost / claim p50 | $0.004360 | $0.004670 | **+$0.000310 (+7.1%)** |
| Cost / claim max | $0.004653 | $0.004916 | +$0.000263 (+5.7%) |
| Latency / claim p50 | 3.45 s | 3.24 s | −0.21 s (within run-to-run noise; not claimed as a saving) |

The token cost lands on **every** claim, not just CLM-002, because the longer description is re-sent on every LLM call. That works out to about 86 extra prompt tokens × 4 calls per claim.

The larger price is behavioural. See §5.

## 5. Regression check: every mode in the taxonomy

| Mode | Before | After | Status |
|---|---|---|---|
| `skipped_required_step` | 3 | 0 | improved (the target) |
| `wrong_argument_choice` | 0 | 0 | checked, unchanged |
| `invalid_argument` | 0 | **1** | **appeared** |
| `repeated_call` | 0 | 0 | checked, unchanged |
| `off_path_call` | 0 | 0 | checked, unchanged (the mitigation did *not* make clean claims read escalated notes) |
| `ordering_violation` | 0 | 0 | checked, unchanged |
| `no_verdict` | 0 | **3** | **NEW, created by the mitigation** |
| `wrong_outcome_despite_evidence` | 0 | 0 | checked, unchanged |

**`no_verdict` (new, 3/3 CLM-002 runs, caused by the mitigation).**
- The correct CLM-002 path needs 5 tool calls. Week 7's `MAX_TOKENS = 4000` budget is checked before each LLM call.
- Every claim already uses about 4.8k tokens across 4 calls, so the 5th call (the one that would submit) is always blocked.
- All three runs stopped with `budget_hit = max_tokens` at about 5,050 tokens, after gathering the right evidence.

This exposes a latent bug from Week 7: the token budget silently caps the agent at 4 LLM calls, so **no** correct 5-step path can finish. The Week 7 eval never saw it because no Week 7 run needed 5 steps.

**Net effect:**
- Outcome pass fell from 100% to 90%.
- Trajectory pass fell from 90% to 86.7%.
- The gap shrank to +3.3 pp, but only because the outcome now fails too. That is not an improvement.

I did **not** raise the budget. That would be a second mitigation, and then we could not tell which change moved the numbers. The honest result is that this mitigation fixes the path and cannot ship on its own. The budget is the next single change to test.

**`invalid_argument` (appeared, 1/30 runs, CLM-007 repeat 1).** The agent submitted `exclusion_applied: "water_damage sub_limit"`. That is fluent fiction, because a sub-limit cap is not an exclusion id. All 3 baseline CLM-007 runs returned `null`. The mitigation did not touch `submit_verdict`, so this may be run-to-run noise. One occurrence in three cannot rule out the longer context as the cause, so it is reported as a worsened mode rather than dismissed.

## Notes and assumptions

- **Cost** uses the Week 7 pricing constants ($0.90 per 1M input and output tokens) so it is comparable with `week7/race.csv`.
- **Latency** excludes Groq 429 back-off sleeps, which are recorded separately as `rate_limit_sleep_s`.
- **Outcome pass** is stricter than Week 7: the verdict **and** the payable amount (within £1) must match.
- **`week8/agent.py`** differs from Week 7 only in tracing. It also feeds malformed tool arguments back as a tool error instead of crashing. That never triggered in these 60 runs.
- **Bonus (indirect injection):** not attempted.
