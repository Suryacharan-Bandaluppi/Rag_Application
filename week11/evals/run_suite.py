"""
Week 11 — coverage eval suite runner.

Runs EVERY case in week11/evals/cases/*.json through the production app (the
current prompt version) and prints the pass count for the whole suite, so a
fix that breaks another case cannot hide behind the new case going green.

Judge (deterministic, per case):
  - decision must equal expected_decision
  - payable: if expected_decision is not_covered, payable must be 0 or null;
    otherwise |payable - expected_payable_gbp| <= 1
  - must_cite_any (optional): at least one of these clause ids is cited

Usage:
    python3 week11/evals/run_suite.py --label before_fix
Writes week11/evals/results/<label>.txt (the exact console output) and .json.
"""

import argparse
import json
import pathlib
import sys
import time

EVALS = pathlib.Path(__file__).resolve().parent
ROOT  = EVALS.parent.parent
sys.path.insert(0, str(ROOT))

from week11.app import answer
from week11.prompts import CURRENT_PROMPT_VERSION

CASES_DIR = EVALS / "cases"
EVAL_LOG  = ROOT / "week11" / "logs" / "eval_traces.jsonl"
TPM_LIMIT = 8000


def judge(case: dict, rec: dict):
    reasons = []
    if rec["decision"] != case["expected_decision"]:
        reasons.append(f"decision {rec['decision']!r} != expected {case['expected_decision']!r}")
    pay = rec.get("payable_gbp")
    if case["expected_decision"] == "not_covered":
        if pay not in (None, 0, 0.0):
            reasons.append(f"payable {pay} on a not_covered case")
    elif "expected_payable_gbp" in case:
        try:
            if abs(float(pay) - case["expected_payable_gbp"]) > 1.0:
                reasons.append(f"payable {pay} != expected {case['expected_payable_gbp']}")
        except (TypeError, ValueError):
            reasons.append(f"payable {pay!r} != expected {case['expected_payable_gbp']}")
    cite = case.get("must_cite_any")
    if cite and not set(cite) & set(rec.get("clauses_cited") or []):
        reasons.append(f"must cite one of {cite}, cited {rec.get('clauses_cited')}")
    return not reasons, reasons


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    a = ap.parse_args()
    cases = [json.loads(p.read_text()) for p in sorted(CASES_DIR.glob("*.json"))]
    lines = [f"SUITE week11/evals/cases  label={a.label}  prompt_version={CURRENT_PROMPT_VERSION}  "
             f"cases={len(cases)}", ""]
    print(lines[0])
    results, passed = [], 0
    for case in cases:
        rec = answer(case["question"], "eval-runner", log_path=EVAL_LOG, env="eval")
        ok, why = judge(case, rec)
        passed += ok
        line = (f"  {'PASS' if ok else 'FAIL'}  {case['case_id']:<4} decision={rec['decision']:<18} "
                f"payable={rec['payable_gbp']!s:<8} cited={rec['clauses_cited']} "
                f"ctx={rec['context_ids']}  trace={rec['trace_id']}"
                + (f"\n        why: {'; '.join(why)}" if why else ""))
        print(line, flush=True)
        lines.append(line)
        results.append({"case_id": case["case_id"], "pass": ok, "why": why,
                        "trace_id": rec["trace_id"], "decision": rec["decision"],
                        "payable_gbp": rec["payable_gbp"], "clauses_cited": rec["clauses_cited"],
                        "context_ids": rec["context_ids"]})
        time.sleep(max(0.0, rec["tokens"] * 60 / TPM_LIMIT - rec["latency_ms"] / 1000))
    total = f"SUITE RESULT: {passed}/{len(cases)} passed  ({'GREEN' if passed == len(cases) else 'RED'})"
    print("\n" + total)
    lines += ["", total]
    out = EVALS / "results"
    out.mkdir(exist_ok=True)
    (out / f"{a.label}.txt").write_text("\n".join(lines) + "\n")
    (out / f"{a.label}.json").write_text(json.dumps(
        {"label": a.label, "prompt_version": CURRENT_PROMPT_VERSION, "passed": passed,
         "total": len(cases), "results": results}, indent=2))


if __name__ == "__main__":
    main()
