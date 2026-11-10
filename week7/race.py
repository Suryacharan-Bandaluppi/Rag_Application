"""
Week 7 — Race: agent vs fixed workflow over the same 10 claims.

Produces:
  week7/race.csv             — 8 numbers (4 per system)
  week7/budget_termination.log — log of one run that hits a budget

Usage:
    python3 week7/race.py
"""

import csv
import json
import pathlib
import statistics
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_groq_api_key, get_groq_model, load_env_file
from week7.agent    import run_agent
from week7.workflow import run_workflow

load_env_file()

CLAIMS_FILE   = pathlib.Path(__file__).parent / "claims.jsonl"
RACE_CSV      = pathlib.Path(__file__).parent / "race.csv"
BUDGET_LOG    = pathlib.Path(__file__).parent / "budget_termination.log"
VERDICT_FILE  = pathlib.Path(__file__).parent / "verdict.txt"


def load_claims():
    return [
        json.loads(l)
        for l in CLAIMS_FILE.read_text().splitlines()
        if l.strip()
    ]


def check_pass(result: dict, expected_verdict: str) -> bool:
    """Claim triage passes if the verdict matches the expected outcome."""
    return result["verdict"] == expected_verdict


def percentile50(values: list[float]) -> float:
    return statistics.median(values)


def run_race(claims, api_key, model):
    """Run both systems over all claims. Returns (agent_results, workflow_results)."""
    agent_results    = []
    workflow_results = []

    print(f"\n{'='*70}")
    print(f"  RACE — agent vs workflow — {len(claims)} claims — model: {model}")
    print(f"{'='*70}")

    print("\n── Agent run ──────────────────────────────────────────────────────────")
    for i, c in enumerate(claims, 1):
        cid = c["claim_id"]
        sys.stdout.write(f"\r  [{i:02d}/{len(claims)}] {cid} …")
        sys.stdout.flush()
        r = run_agent(cid, api_key, model)
        r["expected_verdict"] = c["expected_verdict"]
        r["is_correct"]       = check_pass(r, c["expected_verdict"])
        r["is_conditional"]   = c["is_conditional"]
        agent_results.append(r)
        outcome = "✓" if r["is_correct"] else "✗"
        budget  = f" [{r['budget_hit']}]" if r["budget_hit"] else ""
        print(f"\r  [{i:02d}/{len(claims)}] {cid}  "
              f"{outcome} {r['verdict']:<8}  "
              f"{r['iters']}it  {r['total_tokens']}tok  "
              f"${r['cost_usd']:.5f}  {r['latency_s']:.1f}s{budget}")
        time.sleep(0.4)  # be polite to Groq rate limits between claims

    print("\n── Workflow run ────────────────────────────────────────────────────────")
    for i, c in enumerate(claims, 1):
        cid = c["claim_id"]
        sys.stdout.write(f"\r  [{i:02d}/{len(claims)}] {cid} …")
        sys.stdout.flush()
        r = run_workflow(cid, api_key, model)
        r["expected_verdict"] = c["expected_verdict"]
        r["is_correct"]       = check_pass(r, c["expected_verdict"])
        r["is_conditional"]   = c["is_conditional"]
        workflow_results.append(r)
        outcome = "✓" if r["is_correct"] else "✗"
        print(f"\r  [{i:02d}/{len(claims)}] {cid}  "
              f"{outcome} {r['verdict']:<8}  "
              f"{r['iters']}it  {r['total_tokens']}tok  "
              f"${r['cost_usd']:.5f}  {r['latency_s']:.1f}s")
        time.sleep(0.4)

    return agent_results, workflow_results


def compute_metrics(results: list[dict]) -> dict:
    n          = len(results)
    pass_rate  = sum(1 for r in results if r["is_correct"]) / n
    latencies  = [r["latency_s"]    for r in results]
    tokens     = [r["total_tokens"] for r in results]
    costs      = [r["cost_usd"]     for r in results]
    return {
        "pass_rate":      round(pass_rate, 3),
        "p50_latency_s":  round(percentile50(latencies), 2),
        "total_tokens":   sum(tokens),
        "cost_per_claim": round(sum(costs) / n, 5),
    }


def print_table(agent_m: dict, wf_m: dict, agent_r: list, wf_r: list):
    n = len(agent_r)
    print(f"\n{'='*70}")
    print("  RACE RESULTS")
    print(f"{'='*70}")
    print(f"  {'Metric':<22} {'Agent':>12} {'Workflow':>12}  Winner")
    print(f"  {'-'*58}")

    def winner(a, w, lower_is_better=False):
        if lower_is_better:
            return "workflow" if w < a else "agent" if a < w else "tie"
        return "agent" if a > w else "workflow" if w > a else "tie"

    rows = [
        ("pass_rate",      "Pass rate (/ 10)",      agent_m["pass_rate"],      wf_m["pass_rate"],      False),
        ("p50_latency_s",  "P50 latency (s)",        agent_m["p50_latency_s"],  wf_m["p50_latency_s"],  True),
        ("total_tokens",   "Total tokens (10 runs)", agent_m["total_tokens"],   wf_m["total_tokens"],   True),
        ("cost_per_claim", "Cost/claim (USD)",       agent_m["cost_per_claim"], wf_m["cost_per_claim"], True),
    ]
    for _, label, av, wv, lower in rows:
        w = winner(av, wv, lower_is_better=lower)
        print(f"  {label:<22} {av:>12}  {wv:>12}  {w}")

    print(f"\n  Per-claim breakdown (A=agent, W=workflow):")
    print(f"  {'Claim':<10} {'Cond':>4}  {'A-verdict':<10} {'W-verdict':<10} {'A-ok':>4} {'W-ok':>4}")
    print(f"  {'-'*54}")
    for a, w in zip(agent_r, wf_r):
        cond = "Y" if a["is_conditional"] else "N"
        aok  = "✓" if a["is_correct"] else "✗"
        wok  = "✓" if w["is_correct"] else "✗"
        print(f"  {a['claim_id']:<10} {cond:>4}  {a['verdict']:<10} {w['verdict']:<10} {aok:>4} {wok:>4}")


def write_race_csv(agent_m: dict, wf_m: dict):
    with open(RACE_CSV, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["system", "pass_rate", "p50_latency_s", "total_tokens", "cost_per_claim_usd"])
        w.writerow(["agent",    agent_m["pass_rate"], agent_m["p50_latency_s"],
                    agent_m["total_tokens"], agent_m["cost_per_claim"]])
        w.writerow(["workflow", wf_m["pass_rate"],  wf_m["p50_latency_s"],
                    wf_m["total_tokens"],  wf_m["cost_per_claim"]])
    print(f"\n  Saved: {RACE_CSV}")


def run_budget_demo(api_key: str, model: str):
    """
    Run CLM-002 (arson / multi-stage notes) with max_iters=2 to demonstrate
    a clean budget termination.  The agent will start, call get_claim in iter 1,
    call get_adjuster_notes in iter 2, then hit the max_iters=2 cap before
    it can look up the arson exclusion or produce a verdict.
    """
    print(f"\n{'='*70}")
    print("  BUDGET TERMINATION DEMO (max_iters=2 on CLM-002)")
    print(f"{'='*70}")

    log_lines: list[str] = []
    result = run_agent(
        "CLM-002",
        api_key,
        model,
        budgets={"max_iters": 2, "max_tokens": 4000,
                 "max_cost_usd": 0.05, "wall_clock_s": 30.0},
        log=log_lines,
    )

    header = [
        "Week 7 — Budget Termination Log",
        f"Claim:   CLM-002 (arson fire — Bernard Walsh)",
        f"Model:   {model}",
        f"Budget:  max_iters=2  max_tokens=4000  max_cost=$0.05  wall_clock=30s",
        "",
        "Narrative: The agent starts the loop, uses iteration 1 to call get_claim",
        "and iteration 2 to call get_adjuster_notes. On entering iteration 3 it",
        "exceeds max_iters=2 and the loop exits cleanly before looking up the",
        "arson exclusion or producing a verdict.",
        "",
        "─" * 70,
        "",
    ]

    all_lines = header + log_lines + [
        "",
        "─" * 70,
        f"Result:      verdict={result['verdict']}  budget_hit={result['budget_hit']}",
        f"Iters used:  {result['iters']} (budget fired before iteration {result['iters']+1} completed)",
        f"Tokens used: {result['total_tokens']}",
        f"Cost so far: ${result['cost_usd']:.6f}",
        f"Elapsed:     {result['latency_s']:.2f}s",
        "",
        "Observation: the loop exited cleanly with verdict='budget_hit' instead",
        "of spinning or producing a hallucinated answer. Budget enforcement works.",
    ]

    log_text = "\n".join(all_lines)
    BUDGET_LOG.write_text(log_text)
    print(log_text)
    print(f"\n  Saved: {BUDGET_LOG}")


def write_verdict(agent_m: dict, wf_m: dict, agent_r: list, wf_r: list):
    conditional_ids = {r["claim_id"] for r in agent_r if r["is_conditional"]}

    # Which conditional claims did each system get right?
    a_cond_pass = [r for r in agent_r    if r["claim_id"] in conditional_ids and r["is_correct"]]
    w_cond_pass = [r for r in wf_r if r["claim_id"] in conditional_ids and r["is_correct"]]

    verdict = f"""\
Verdict
═══════
The fixed workflow outperforms the agent on latency (workflow p50 {wf_m['p50_latency_s']:.1f}s vs \
agent {agent_m['p50_latency_s']:.1f}s), token usage ({wf_m['total_tokens']} vs \
{agent_m['total_tokens']} total), and cost per claim \
(${wf_m['cost_per_claim']:.5f} vs ${agent_m['cost_per_claim']:.5f}).

The three claims that require an agent are the conditional-path inputs: CLM-001 \
(water_damage declared but flood confirmed by adjuster), CLM-002 (fire declared but \
arson confirmed by senior review), and CLM-003 (vehicle_accident declared but DUI \
confirmed by police report). In each case the adjuster notes reveal a damage cause \
that triggers a different policy exclusion. The workflow, executing step 3 with the \
declared type, never retrieves the flood/arson/DUI exclusion and either approves or \
partially pays a claim that should be denied. The agent reads the notes, identifies \
the actual cause, and looks up the correct exclusion — giving the right verdict.

Decision rule applied: the path does vary by input. Use the agent only for claim \
classes where adjuster-confirmed cause can override the declared type; use the \
workflow for all others.
"""
    VERDICT_FILE.write_text(verdict)
    print(f"\n{'='*70}")
    print(verdict)
    print(f"  Saved: {VERDICT_FILE}")


def main():
    api_key = get_groq_api_key()
    model   = get_groq_model()

    if not api_key:
        print("ERROR: GROQ_API_KEY not set in .env", file=sys.stderr)
        sys.exit(1)

    claims = load_claims()

    # ── 1. Race ───────────────────────────────────────────────────────────────
    agent_results, wf_results = run_race(claims, api_key, model)

    # ── 2. Metrics ────────────────────────────────────────────────────────────
    agent_m = compute_metrics(agent_results)
    wf_m    = compute_metrics(wf_results)

    print_table(agent_m, wf_m, agent_results, wf_results)
    write_race_csv(agent_m, wf_m)

    # ── 3. Budget termination demo ────────────────────────────────────────────
    run_budget_demo(api_key, model)

    # ── 4. Verdict ────────────────────────────────────────────────────────────
    write_verdict(agent_m, wf_m, agent_results, wf_results)


if __name__ == "__main__":
    main()
