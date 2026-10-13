"""
Week 10 — Race: single agent vs orchestrator on the SAME 10 claim cases.

Eval set : week7/claims.jsonl, the 10 claims both agents were built on (CLM-001..010).
           Unchanged; no new cases.
Judge    : week8.trajectory_eval.outcome_pass (verdict AND payable within GBP 1),
           imported, not re-implemented. The same judge scores both arms.
Single   : week8.agent.run_agent_traced(variant="baseline"), i.e. the week7 agent
Multi    : week10.orchestrator.run_orchestrator + the two HTTP workers

Commands:
    python3 week10/race.py race       # both arms x 10 cases -> evidence/race_results.jsonl
    python3 week10/race.py failure    # exclusions worker returns HTTP 500 on CLM-001, 3 runs
    python3 week10/race.py report     # race_table.md, handoffs.log, failure evidence summary
"""

import json
import math
import pathlib
import statistics
import sys
import time
from collections import defaultdict

WEEK10 = pathlib.Path(__file__).resolve().parent
ROOT   = WEEK10.parent
sys.path.insert(0, str(ROOT))

from app.config import get_groq_api_key, get_groq_model
from week7.tools import _claims
from week8.trajectory_eval import outcome_pass          # the judge — unchanged

EVIDENCE      = WEEK10 / "evidence"
RACE_RAW      = EVIDENCE / "race_results.jsonl"
FAIL_RAW      = EVIDENCE / "failure_runs.jsonl"
HANDOFFS_LOG  = WEEK10 / "handoffs.log"
RACE_TABLE    = WEEK10 / "race_table.md"
FAIL_CASE     = "CLM-001"
FAIL_WORKER   = "exclusions"
FAIL_REPEATS  = 3
TPM_LIMIT     = 8000

CLAIMS = _claims()
CASES  = list(CLAIMS)          # CLM-001 .. CLM-010, file order


def _pace(tokens, latency):
    """Stay under the Groq tokens-per-minute limit between runs."""
    time.sleep(max(0.0, tokens * 60 / TPM_LIMIT - latency))


def _single(cid, api_key, model):
    from week8.agent import run_agent_traced
    r = run_agent_traced(cid, api_key, model, variant="baseline")
    r["arm"] = "single"
    return r


def cmd_race():
    from week10.orchestrator import run_orchestrator
    from week10.workers import start_worker_server, clear_failures
    clear_failures()
    api_key, model = get_groq_api_key(), get_groq_model()
    url = start_worker_server()
    EVIDENCE.mkdir(exist_ok=True)
    with open(RACE_RAW, "w") as f:
        for i, cid in enumerate(CASES):
            # alternate which arm goes first, so neither always gets a "fresh" rate-limit window
            order = ["single", "orchestrator"] if i % 2 == 0 else ["orchestrator", "single"]
            for arm in order:
                r = _single(cid, api_key, model) if arm == "single" else run_orchestrator(cid, url)
                r["model"] = model
                r["pass"] = outcome_pass(CLAIMS[cid], r)
                f.write(json.dumps(r) + "\n")
                f.flush()
                print(f"  {cid} {arm:<12} {'PASS' if r['pass'] else 'FAIL'}  "
                      f"{r['verdict']:<8} £{r['payable_amount']:<9.2f} "
                      f"{r['total_tokens']:>6} tok  ${r['cost_usd']:.6f}  {r['latency_s']:.2f}s")
                _pace(r["total_tokens"], r["latency_s"])


def cmd_failure():
    from week10.orchestrator import run_orchestrator
    from week10.workers import start_worker_server, inject_failure, clear_failures
    url = start_worker_server()
    clear_failures()
    inject_failure(FAIL_WORKER, FAIL_CASE)
    EVIDENCE.mkdir(exist_ok=True)
    with open(FAIL_RAW, "w") as f:
        for rep in range(1, FAIL_REPEATS + 1):
            r = run_orchestrator(FAIL_CASE, url)
            r["repeat"] = rep
            r["model"] = get_groq_model()
            r["pass"] = outcome_pass(CLAIMS[FAIL_CASE], r)
            r["behaviour"] = classify_failure(r)
            f.write(json.dumps(r) + "\n")
            f.flush()
            print(f"  repeat {rep}: verdict={r['verdict']} £{r['payable_amount']} "
                  f"excl={r['exclusion_applied']!r} -> {r['behaviour']}")
            print(f"    reason: {r['reason']}")
            _pace(r["total_tokens"], r["latency_s"])
    clear_failures()


def classify_failure(r: dict) -> dict:
    """Mechanical facts about a run where the exclusions worker failed."""
    calls = [e for e in r["events"] if e["event"] == "worker_call" and e["worker"] == FAIL_WORKER]
    ok = [e for e in calls if e["http_status"] == 200]
    failed = [e for e in calls if e["http_status"] != 200]
    if r["verdict"] in ("approved", "partial") and not ok:
        label = "LIED: declared the claim payable with no successful exclusions check"
    elif r["verdict"] == "no_verdict":
        label = "DEGRADED: no verdict submitted"
    elif r["verdict"] == "denied" and not ok:
        label = "DECIDED WITHOUT EXCLUSIONS CHECK: denied from the adjuster notes alone"
    else:
        label = "RECOVERED: exclusions check eventually succeeded"
    return {"exclusions_attempts": len(calls), "http_500s": len(failed),
            "retried": len(calls) > 1, "label": label}


# ── report ────────────────────────────────────────────────────────────────────

def _p(values, q):
    """Nearest-rank percentile (q in 0..100)."""
    s = sorted(values)
    return s[max(0, math.ceil(q / 100 * len(s)) - 1)]


def _metrics(runs):
    lat = [r["latency_s"] for r in runs]
    return {
        "n": len(runs),
        "passed": sum(r["pass"] for r in runs),
        "pass_rate": sum(r["pass"] for r in runs) / len(runs),
        "p50": statistics.median(lat),
        "p99": _p(lat, 99),
        "max": max(lat),
        "tokens": sum(r["total_tokens"] for r in runs),
        "cost_total": sum(r["cost_usd"] for r in runs),
        "cost_per_claim": sum(r["cost_usd"] for r in runs) / len(runs),
        "cost_p50": statistics.median(r["cost_usd"] for r in runs),
        "cost_max": max(r["cost_usd"] for r in runs),
        "llm_calls": sum(r["llm_calls"] for r in runs) / len(runs),
    }


def cmd_report():
    runs = [json.loads(l) for l in RACE_RAW.read_text().splitlines() if l.strip()]
    S = [r for r in runs if r["arm"] == "single"]
    M = [r for r in runs if r["arm"] == "orchestrator"]
    assert [r["claim_id"] for r in S] == CASES and [r["claim_id"] for r in M] == CASES, \
        "both arms must cover the same 10 cases"
    s, m = _metrics(S), _metrics(M)

    # hand-off attribution (multi arm)
    by_label, grand = defaultdict(int), m["tokens"]
    for r in M:
        for h in r["handoffs"]:
            by_label[h["handoff"]] += h["total_tokens"]
    ranked = sorted(by_label.items(), key=lambda kv: -kv[1])
    top_label, top_tokens = ranked[0]
    multiplier = m["tokens"] / s["tokens"]
    mult_line = (f"Context re-send multiplier: {m['tokens']:,} / {s['tokens']:,} = "
                 f"{multiplier:.1f}x  —  largest share: \"{top_label}\" "
                 f"{top_tokens:,} tokens = {100 * top_tokens / grand:.0f}% of all orchestrator-arm tokens")

    # synthesis fidelity: did the verdict contradict the exclusions worker?
    contradictions = []
    for r in M:
        cov = [json.loads(e["result"]) for e in r["events"]
               if e["event"] == "worker_call" and e["worker"] == "exclusions" and e["http_status"] == 200]
        if cov and cov[-1].get("payout_modifier") == 0 and r["verdict"] != "denied":
            contradictions.append(r["claim_id"])

    # handoffs.log
    L = ["# handoffs.log — every hand-off in the race, with token counts",
         f"# model={runs[0]['model']}  pricing=$0.90/1M tokens in+out (week7 constants)",
         "# Orchestrator arm: one line per LLM call, labelled by the hand-off that caused it.",
         "#   'user -> orchestrator'          = orchestrator's first call",
         "#   'X -> orchestrator'             = orchestrator call that re-sends its full history after X returned",
         "#   'orchestrator -> W worker'      = an LLM call inside worker W (gets only its narrow input)",
         "# Single arm: no hand-offs (one agent); one total line per case for comparison.",
         "#",
         f"# {'case':<8} {'arm':<12} {'seq':>3}  {'hand-off':<42} {'agent':<18} "
         f"{'prompt':>6} {'compl':>6} {'total':>6}"]
    for cid in CASES:
        r = next(x for x in S if x["claim_id"] == cid)
        L.append(f"  {cid:<8} {'single':<12} {'-':>3}  {'(no hand-off: single agent, ' + str(r['llm_calls']) + ' calls)':<42} "
                 f"{'single_agent':<18} {r['prompt_tokens']:>6} {r['completion_tokens']:>6} {r['total_tokens']:>6}")
        r = next(x for x in M if x["claim_id"] == cid)
        for i, h in enumerate(r["handoffs"], 1):
            L.append(f"  {cid:<8} {'orchestrator':<12} {i:>3}  {h['handoff']:<42} {h['agent']:<18} "
                     f"{h['prompt_tokens']:>6} {h['completion_tokens']:>6} {h['total_tokens']:>6}")
        L.append(f"  {cid:<8} {'orchestrator':<12} {'':>3}  {'= case total':<42} {'':<18} "
                 f"{r['prompt_tokens']:>6} {r['completion_tokens']:>6} {r['total_tokens']:>6}")
    L += ["#", "# Totals by hand-off (orchestrator arm, all 10 cases)"]
    for label, tok in ranked:
        n = sum(1 for r in M for h in r["handoffs"] if h["handoff"] == label)
        L.append(f"  {label:<42} {n:>3} calls  {tok:>7,} tokens  {100 * tok / grand:5.1f}%")
    L += [f"  {'orchestrator arm total':<42} {sum(r['llm_calls'] for r in M):>3} calls  {grand:>7,} tokens  100.0%",
          f"  {'single arm total':<42} {sum(r['llm_calls'] for r in S):>3} calls  {s['tokens']:>7,} tokens",
          "#", "# " + mult_line]
    HANDOFFS_LOG.write_text("\n".join(L) + "\n")

    # race_table.md
    T = ["# race_table.md — single agent vs orchestrator, same 10 cases, same judge\n",
         "**Cases (unchanged):** " + ", ".join(CASES) + " — the 10 claim cases in "
         "`week7/claims.jsonl` that the single agent was built and evaluated on (Week 7 race, "
         "Week 8 trajectory eval).  ",
         "**Judge (unchanged, imported):** `week8/trajectory_eval.outcome_pass`. A case "
         "passes if the verdict matches AND the payable amount is within £1.  ",
         f"**Model:** `{runs[0]['model']}`, temperature 0, one run per case per arm, arms "
         "interleaved. Latency excludes Groq 429 back-off. Cost uses $0.90 per 1M tokens (week7 constants).\n",
         "> Why not `week6/eval_cases.jsonl`? In this repo Week 6 evaluated the PaySlate "
         "**developer-docs RAG** app (25 questions such as *\"minimum Dart SDK version\"*). "
         "It contains no insurance claims, so a claims agent cannot be run on it. The claims "
         "eval set that already exists, and that the single agent was scored on, is the 10 "
         "cases above. No case was added, removed or edited.\n",
         "| Metric | Single agent | Orchestrator | Δ |",
         "|---|---|---|---|",
         f"| **Pass rate** | {s['passed']}/10 = {100 * s['pass_rate']:.0f}% | "
         f"{m['passed']}/10 = {100 * m['pass_rate']:.0f}% | {100 * (m['pass_rate'] - s['pass_rate']):+.0f} pp |",
         f"| **Latency p50** | {s['p50']:.2f} s | {m['p50']:.2f} s | {m['p50'] / s['p50']:.1f}x |",
         f"| **Latency p99** | {s['p99']:.2f} s | {m['p99']:.2f} s | {m['p99'] / s['p99']:.1f}x |",
         f"| **Total tokens** (10 cases) | {s['tokens']:,} | {m['tokens']:,} | {m['tokens'] / s['tokens']:.1f}x |",
         f"| **Cost per claim** (mean) | ${s['cost_per_claim']:.6f} | ${m['cost_per_claim']:.6f} | "
         f"{m['cost_per_claim'] / s['cost_per_claim']:.1f}x |",
         "",
         "Supporting numbers:\n",
         "| | Single agent | Orchestrator |",
         "|---|---|---|",
         f"| Cost per claim p50 / max | ${s['cost_p50']:.6f} / ${s['cost_max']:.6f} | "
         f"${m['cost_p50']:.6f} / ${m['cost_max']:.6f} |",
         f"| Total cost, 10 claims | ${s['cost_total']:.6f} | ${m['cost_total']:.6f} |",
         f"| LLM calls per claim (mean) | {s['llm_calls']:.1f} | {m['llm_calls']:.1f} |",
         f"| Slowest case (= p99 with n=10, nearest rank) | {s['max']:.2f} s "
         f"({max(S, key=lambda r: r['latency_s'])['claim_id']}) | {m['max']:.2f} s "
         f"({max(M, key=lambda r: r['latency_s'])['claim_id']}) |",
         "",
         "p99 over 10 runs is the nearest-rank 99th percentile, which is the slowest run; "
         "it is reported, not hidden.\n",
         "## Per-case results\n",
         "| Case | Expected | Single | Orchestrator |",
         "|---|---|---|---|"]
    for cid in CASES:
        c = CLAIMS[cid]
        a = next(x for x in S if x["claim_id"] == cid)
        b = next(x for x in M if x["claim_id"] == cid)
        fmt = lambda r: (f"{'✅' if r['pass'] else '❌'} {r['verdict']} £{r['payable_amount']:.0f} · "
                         f"{r['total_tokens']:,} tok · {r['latency_s']:.1f}s")
        T.append(f"| {cid} | {c['expected_verdict']} £{c['expected_payable']:.0f} | {fmt(a)} | {fmt(b)} |")
    T += ["", "## Multiplier\n", f"**{mult_line}**\n",
          "Top hand-offs by token share (full breakdown in `handoffs.log`):\n",
          "| Hand-off | Tokens | Share |", "|---|---|---|"]
    for label, tok in ranked[:5]:
        T.append(f"| {label} | {tok:,} | {100 * tok / grand:.1f}% |")
    T += ["", "## Synthesis fidelity check\n",
          f"Cases where the exclusions worker returned payout_modifier 0 but the orchestrator "
          f"did not deny: **{len(contradictions)}** {contradictions or ''}.\n"]
    RACE_TABLE.write_text("\n".join(T) + "\n")
    print("\n".join(T))
    print(f"\nwrote {RACE_TABLE.relative_to(ROOT)} and {HANDOFFS_LOG.relative_to(ROOT)}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    {"race": cmd_race, "failure": cmd_failure, "report": cmd_report}.get(
        cmd, lambda: sys.exit("usage: race.py race|failure|report"))()
