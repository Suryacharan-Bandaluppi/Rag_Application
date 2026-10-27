# Week 10 · Task Set D — Race the claims squad against the single agent

**Question:** does a multi-agent orchestrator beat the single claims agent, or does it just cost more per claim?
**Answer:** it only costs more. Same pass rate, 1.6x the tokens and cost, 2.2x the p50 latency, and under a worker failure it fabricates a policy check. **Kill it** (`verdict.md`).

## The two arms

| | Single agent | Orchestrator (squad) |
|---|---|---|
| Code | `week8/agent.py` (`run_agent_traced`, baseline): the Week 7 claims agent, unchanged | `week10/orchestrator.py` + `week10/workers.py` |
| Agents | 1 | orchestrator + notes worker + coverage/exclusions worker |
| Tools | get_claim, get_adjuster_notes, search_policy_exclusions, submit_verdict | Orchestrator: get_claim, ask_notes_worker, ask_exclusions_worker, submit_verdict<br>Notes worker: **only** get_adjuster_notes<br>Exclusions worker: **only** search_policy_exclusions |
| Output contract | `submit_verdict` | the same `submit_verdict`, with the same payout rules |

**Design choices that answer the brief's "common mistakes":**
- **Narrow context.** The notes worker receives only a claim id. The exclusions worker receives only `{cause, declared_amount}`. Neither sees the note history or the other's output, so we test the pattern, not a wasteful context strategy.
- **Narrow tools.** Each worker has exactly one domain tool.
- **Qualifiers survive synthesis.** The exclusions worker must say "covered subject to …", and the orchestrator must keep it. CLM-007 came back as "covered subject to sub-limit of GBP 8100" and was settled as partial £7,600. Automatic check: 0 cases where a 0-modifier result was turned into a non-denial.
- **Real HTTP.** Workers sit behind a local HTTP server, so the injected failure is a real HTTP 500.
- **No failure script.** The orchestrator has no special failure handling in its prompt, so the race records what it actually does.

## Eval set and judge: the deviation from the brief, stated plainly
The brief says to use "the same 10 Week-6 eval cases". In this repo, `week6/eval_cases.jsonl` is 25 questions about the PaySlate **developer-docs RAG app** (e.g. "minimum Dart SDK version"). There is not a single insurance claim in it, so a claims agent cannot run on it.

The rule behind the brief is "don't change the ruler". So both arms run on the claims eval set that already exists: the **10 cases in `week7/claims.jsonl`** (CLM-001…010), the set the single agent was raced on in Week 7 and trajectory-evaluated on in Week 8. No case was added, removed or edited. The judge is the existing `week8/trajectory_eval.outcome_pass` (verdict + payable within £1), imported unchanged, and it scores both arms.

## Deliverables

| Checklist item | File | Headline |
|---|---|---|
| 4 metrics × 2 arms, cases named | `race_table.md` | Pass 10/10 vs 10/10 · p50 3.54 s vs 7.77 s · p99 6.22 s vs 10.07 s · tokens 48,305 vs 74,879 · cost/claim $0.004347 vs $0.006739 |
| Every hand-off with token counts | `handoffs.log` | 81 orchestrator-arm LLM calls, each labelled by hand-off, plus per-label totals |
| Multiplier line with the dominant hand-off | `race_table.md`, end of `handoffs.log` | **1.6x**; largest: "orchestrator -> exclusions worker" = **24%** |
| Injected 500 and what happened | `failure_case.md` | **Retried, then lied** (3/3 runs). The judge passed all 3. |
| Keep/kill, ≥2 numbers, sunk cost named | `verdict.md` (8 lines) | **KILL** |
| Bonus: AgentCard and A2A lifecycle | `agent_card.json`, `a2a_lifecycle.md` | Should have paused in `input-required` |

### Reading the multiplier
The single largest hand-off is the exclusions worker's own calls (24%), nearly tied with the notes worker (23%). Each worker pays for its own system prompt and tool schemas on every call; that overhead doesn't exist in a single agent.

Grouped by who pays, the orchestrator re-sending its own growing history to itself (`user/get_claim/notes worker/exclusions worker -> orchestrator`) is **53%** of all tokens. That re-send is the structural cost of the pattern, even with narrow worker context.

### The failure finding
With the exclusions worker at HTTP 500 on CLM-001:
1. The orchestrator retried 2–3 times.
2. It then denied the claim "per policy exclusions" it never received, citing a made-up id `flood_exclusion` (the real one is `all_flood_damage`).
3. The outcome was right only because the notes worker's summary happened to name the flood exclusion.

The existing outcome judge cannot see this; it scored all 3 runs PASS.

## Limits (honest)
- **One run per case per arm**, as the brief specifies. With n=10, the nearest-rank p99 is the slowest run (CLM-006 in both arms). It is reported, not hidden.
- **Pricing.** Cost uses the Week 7 pricing constant ($0.90/1M tokens) for comparability.
- **Latency** excludes Groq 429 back-off in both arms.
- **The single agent keeps its Week 7 budgets.** As found in Week 8, its `MAX_TOKENS=4000` caps it at 4 LLM calls. It completed all 10 cases within that, so the cap did not affect this race.
- **One failure case.** The failure was injected on one case (CLM-001, chosen before running). "Would it approve a clean claim with the exclusions service down?" was not tested; `failure_case.md` lists it as a risk.

## Re-run
```bash
python3 week10/race.py race       # ~15 min (rate-limited), writes evidence/race_results.jsonl
python3 week10/race.py failure    # 3 runs with the exclusions worker returning HTTP 500
python3 week10/race.py report     # race_table.md + handoffs.log
python3 -m week10.orchestrator CLM-002   # one orchestrator run, printed hand-off by hand-off
```
