"""
Week 8 — Trajectory eval for the claims agent.

The week7 race scored only the OUTCOME (verdict). This file scores the PATH:
which tools the agent called, with which arguments, in which order, and what
each claim cost.

Commands:
    python3 week8/trajectory_eval.py selftest
        Assert that every expected sequence scores as a pass and that known-bad
        paths are caught with the right failure mode. No API calls.

    python3 week8/trajectory_eval.py run --variant baseline  [--repeats 3]
    python3 week8/trajectory_eval.py run --variant mitigated [--repeats 3]
        Run the agent over the 10 claims (x repeats) and save raw traces to
        week8/traces_<variant>.jsonl.

    python3 week8/trajectory_eval.py report
        Score the saved traces and write week8/results.md.

Step signatures
───────────────
A tool call is reduced to a signature that captures the routing decision:
    claim             get_claim(claim_id)
    notes:<status>    get_adjuster_notes(claim_id, status)
    excl:<cause>      search_policy_exclusions(cause)
    submit            submit_verdict(...)
"""

import argparse
import json
import pathlib
import re
import statistics
import sys
import time
from collections import Counter

ROOT  = pathlib.Path(__file__).resolve().parent.parent
WEEK8 = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from week7.tools import POLICY_DB, VALID_CAUSES, VALID_STATUSES, _claims

CLAIMS = _claims()
RESULTS_MD = WEEK8 / "results.md"


def traces_path(variant: str) -> pathlib.Path:
    return WEEK8 / f"traces_{variant}.jsonl"


# ═════════════════════════════════════════════════════════════════════════════
# 1. EXPECTED TOOL SEQUENCES — 10 claim cases
# ═════════════════════════════════════════════════════════════════════════════
#
# Every case accepts MORE THAN ONE valid path, asserted as a set:
#
#   (a) claim/notes swap — get_claim and get_adjuster_notes("reviewed") are
#       independent reads keyed only on claim_id. Reading the notes before or
#       after pulling the claim record are both correct.  [all 10 cases]
#
#   (b) clean claims (actual cause == declared type): the exclusion lookup for
#       the declared type may come BEFORE the notes are read, because the
#       notes only confirm the cause.  [CLM-004..010]
#
#   (c) conditional claims (notes reveal a different cause): the agent may
#       look up the declared type first, as long as it then reads the notes
#       and looks up the ACTUAL cause before submitting. The extra lookup costs
#       a step (counted in step efficiency) but the path is correct.
#       [CLM-001, CLM-002, CLM-003]
#
#   (d) CLM-002 only: the reviewed notes say arson is "under consideration
#       pending investigation outcome. Senior review recommended." The
#       confirmation lives ONLY in the 'escalated' notes. So notes:escalated
#       is a required step; it may come before or after the arson lookup.
#
# What is NOT accepted (each is a real failure the eval must catch):
#   - submitting without ever calling search_policy_exclusions
#   - looking up only the declared type on a conditional claim
#   - denying CLM-002 on an unconfirmed inquiry (no escalated notes read)
#   - looking up the actual cause before reading the notes (a guess)

C, S = "claim", "submit"
NR, NE = "notes:reviewed", "notes:escalated"


def _clean(declared: str) -> set:
    x = f"excl:{declared}"
    return {
        (C,  NR, x,  S),
        (NR, C,  x,  S),     # alternate (a)
        (C,  x,  NR, S),     # alternate (b)
    }


def _conditional(declared: str, actual: str) -> set:
    d, a = f"excl:{declared}", f"excl:{actual}"
    return {
        (C,  NR, a,  S),
        (NR, C,  a,  S),     # alternate (a)
        (C,  d,  NR, a, S),  # alternate (c)
    }


EXPECTED = {
    # id         accepted sequences                          actual cause
    "CLM-001": _conditional("water_damage", "flood"),
    "CLM-002": {
        (C,  NR, NE, "excl:arson", S),
        (NR, C,  NE, "excl:arson", S),                       # alternate (a)
        (NR, NE, C,  "excl:arson", S),                       # alternate (a)
        (C,  NR, "excl:arson", NE, S),                       # alternate (d)
        (NR, C,  "excl:arson", NE, S),                       # alternate (a)+(d)
        (C,  "excl:fire", NR, NE, "excl:arson", S),          # alternate (c)
    },
    "CLM-003": _conditional("vehicle_accident", "intoxicated_driving"),
    "CLM-004": _clean("theft"),
    "CLM-005": _clean("hail_damage"),
    "CLM-006": _clean("vandalism"),
    "CLM-007": _clean("water_damage"),
    "CLM-008": _clean("tree_damage"),
    "CLM-009": _clean("lightning"),
    "CLM-010": _clean("theft"),
}

# Hard assertions on the spec itself, so a typo in EXPECTED fails loudly.
assert set(EXPECTED) == set(CLAIMS), "EXPECTED must cover exactly the 10 claims"
for _cid, _paths in EXPECTED.items():
    assert len(_paths) >= 2, f"{_cid}: alternate paths must be asserted as a set"
    for _p in _paths:
        assert _p[-1] == S, f"{_cid}: every path must end with submit"
        for _sig in _p:
            if _sig.startswith("excl:"):
                assert _sig[5:] in VALID_CAUSES, f"{_cid}: unknown cause {_sig}"
            if _sig.startswith("notes:"):
                assert _sig[6:] in VALID_STATUSES, f"{_cid}: unknown status {_sig}"


def required_steps(cid: str) -> set:
    """Steps that appear in EVERY accepted path for this case."""
    paths = [set(p) for p in EXPECTED[cid]]
    return set.intersection(*paths)


def steps_needed(cid: str) -> int:
    return min(len(p) for p in EXPECTED[cid])


# ═════════════════════════════════════════════════════════════════════════════
# 2. ARGUMENT VALIDITY — real ids or fluent fiction?
# ═════════════════════════════════════════════════════════════════════════════

ALL_EXCLUSION_IDS = {e for row in POLICY_DB.values() for e in row["exclusions"]}
CLAUSE_RE = re.compile(r"Clause\s+(\d+(?:\.\d+)?[a-z]?)", re.I)


def signature(step: dict) -> str:
    tool, args = step["tool"], step.get("args") or {}
    if step.get("args_parse_error"):
        return f"{tool}:<unparseable>"
    if tool == "get_claim":
        return C
    if tool == "get_adjuster_notes":
        return f"notes:{args.get('status')}"
    if tool == "search_policy_exclusions":
        return f"excl:{args.get('cause')}"
    if tool == "submit_verdict":
        return S
    if tool == "<text>":
        return "<text>"
    return f"unknown_tool:{tool}"


def _grounded_exclusions(trajectory: list) -> tuple:
    """Exclusion ids and clause ids the agent actually saw in tool results."""
    ids, clauses = set(), set()
    for s in trajectory:
        if s["tool"] != "search_policy_exclusions":
            continue
        try:
            res = json.loads(s["result"])
        except Exception:
            continue
        ids.update(res.get("exclusions") or [])
        clauses.update(m.lower() for m in CLAUSE_RE.findall(res.get("description", "")))
    return ids, clauses


def check_arguments(cid: str, trajectory: list) -> list:
    """
    One entry per checked argument:
        {"step": i, "tool": ..., "arg": ..., "value": ..., "valid": bool, "why": str}
    """
    out = []
    seen_ids, seen_clauses = _grounded_exclusions(trajectory)

    def rec(i, tool, arg, value, valid, why=""):
        out.append({"step": i, "tool": tool, "arg": arg, "value": value,
                    "valid": valid, "why": why})

    for i, s in enumerate(trajectory):
        tool, args = s["tool"], s.get("args") or {}
        if s.get("args_parse_error"):
            rec(i, tool, "<arguments>", s.get("args_raw"), False, "not valid JSON")
            continue
        if tool in ("get_claim", "get_adjuster_notes"):
            v = args.get("claim_id")
            if v not in CLAIMS:
                rec(i, tool, "claim_id", v, False, "claim number does not exist")
            elif v != cid:
                rec(i, tool, "claim_id", v, False, f"real claim but not {cid}")
            else:
                rec(i, tool, "claim_id", v, True)
        if tool == "get_adjuster_notes":
            v = args.get("status")
            rec(i, tool, "status", v, v in VALID_STATUSES,
                "" if v in VALID_STATUSES else "not a review stage")
        if tool == "search_policy_exclusions":
            v = args.get("cause")
            rec(i, tool, "cause", v, v in VALID_CAUSES,
                "" if v in VALID_CAUSES else "not a policy cause")
        if tool == "submit_verdict":
            v = args.get("verdict")
            ok = v in ("approved", "partial", "denied")
            rec(i, tool, "verdict", v, ok, "" if ok else "not in verdict enum")

            v = args.get("exclusion_applied")
            if v in (None, "", "null", "None"):
                rec(i, tool, "exclusion_applied", v, True)
            else:
                txt = str(v)
                hit_id = [e for e in ALL_EXCLUSION_IDS if e in txt]
                hit_cl = [m.lower() for m in CLAUSE_RE.findall(txt)]
                if any(e in seen_ids for e in hit_id) or any(c in seen_clauses for c in hit_cl):
                    rec(i, tool, "exclusion_applied", v, True)
                elif hit_id or hit_cl:
                    rec(i, tool, "exclusion_applied", v, False,
                        "real exclusion id but never returned by a lookup in this run")
                else:
                    rec(i, tool, "exclusion_applied", v, False,
                        "fluent fiction: not an exclusion id or clause in the policy")
        if tool not in ("get_claim", "get_adjuster_notes",
                        "search_policy_exclusions", "submit_verdict", "<text>"):
            rec(i, tool, "<tool>", tool, False, "tool does not exist")
    return out


# ═════════════════════════════════════════════════════════════════════════════
# 3. FAILURE-MODE TAXONOMY (the Week 8 zoo, as it shows up in this agent)
# ═════════════════════════════════════════════════════════════════════════════

MODES = [
    ("skipped_required_step",
     "Required evidence never gathered (e.g. no exclusion lookup, CLM-002 "
     "escalated notes never read). Right answer possible by luck."),
    ("wrong_argument_choice",
     "Right tool, wrong routing argument — e.g. exclusions looked up for the "
     "DECLARED type instead of the actual cause."),
    ("invalid_argument",
     "Fluent fiction: a claim number, status, cause or exclusion id that is "
     "not real, or not grounded in a tool result from this run."),
    ("repeated_call",
     "Same call made more than once (looping)."),
    ("off_path_call",
     "A call outside every accepted path (unneeded tool or stage)."),
    ("ordering_violation",
     "All the right calls, but in an order no accepted path allows "
     "(e.g. actual-cause lookup before the notes were read = a guess)."),
    ("no_verdict",
     "Budget hit or the agent stopped without calling submit_verdict."),
    ("wrong_outcome_despite_evidence",
     "All required evidence gathered, verdict/payout still wrong (ignored "
     "or mis-computed the tool output)."),
]
MODE_NAMES = [m for m, _ in MODES]


def _lcs(a: tuple, b: tuple) -> int:
    dp = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a)):
        for j in range(len(b)):
            dp[i + 1][j + 1] = dp[i][j] + 1 if a[i] == b[j] else max(dp[i][j + 1], dp[i + 1][j])
    return dp[-1][-1]


def closest_path(cid: str, seq: tuple) -> tuple:
    """Accepted path with the smallest multiset difference, then highest LCS."""
    def key(p):
        a, e = Counter(seq), Counter(p)
        diff = sum((e - a).values()) + sum((a - e).values())
        return (diff, -_lcs(seq, p), len(p), p)
    return min(EXPECTED[cid], key=key)


def outcome_pass(case: dict, run: dict) -> bool:
    return (run["verdict"] == case["expected_verdict"]
            and abs(run["payable_amount"] - case["expected_payable"]) <= 1.0)


def score_run(run: dict) -> dict:
    cid   = run["claim_id"]
    case  = CLAIMS[cid]
    traj  = run["trajectory"]
    seq   = tuple(signature(s) for s in traj)
    core  = tuple(s for s in seq if s != "<text>")
    args  = check_arguments(cid, traj)
    args_ok = all(a["valid"] for a in args)

    best    = closest_path(cid, core)
    req     = required_steps(cid)
    missing = Counter(best) - Counter(core)
    extra   = Counter(core) - Counter(best)
    no_verdict = run["verdict"] == "no_verdict" or run.get("budget_hit") is not None
    if no_verdict:
        missing.pop(S, None)

    modes = set()
    substituted_tools = set()
    for sig in missing:
        tool = sig.split(":")[0]
        subs = [x for x in core if x.split(":")[0] == tool and x not in req]
        if subs:
            modes.add("wrong_argument_choice")
            substituted_tools.add(tool)
        else:
            modes.add("skipped_required_step")
    for sig in extra:
        if core.count(sig) > 1:
            modes.add("repeated_call")
        elif sig.split(":")[0] in substituted_tools:
            pass                      # already counted as wrong_argument_choice
        else:
            modes.add("off_path_call")
    if not missing and not extra and core not in EXPECTED[cid] and not no_verdict:
        modes.add("ordering_violation")
    if not args_ok:
        modes.add("invalid_argument")
    if no_verdict:
        modes.add("no_verdict")

    o_pass = outcome_pass(case, run)
    if not o_pass and not no_verdict and not (set(req) - set(core) - {S}):
        modes.add("wrong_outcome_despite_evidence")

    t_pass = (seq in EXPECTED[cid]) and args_ok
    assert t_pass or (modes - {"wrong_outcome_despite_evidence"}), (
        f"{cid}: trajectory failed but no trajectory mode explains it: {seq}")
    assert not (t_pass and (modes - {"wrong_outcome_despite_evidence"})), (
        f"{cid}: trajectory passed but modes fired: {modes}")

    taken  = len(seq)
    needed = steps_needed(cid)
    lcs    = _lcs(core, best)
    return {
        "claim_id":        cid,
        "sequence":        list(seq),
        "closest_expected": list(best),
        "missing":         sorted(missing.elements()),
        "extra":           sorted(extra.elements()),
        "outcome_pass":    o_pass,
        "trajectory_pass": t_pass,
        "tool_choice_correct": lcs,
        "tool_choice_total":   max(len(core), len(best)),
        "args_valid":      sum(a["valid"] for a in args),
        "args_total":      len(args),
        "invalid_args":    [a for a in args if not a["valid"]],
        "steps_taken":     taken,
        "steps_needed":    needed,
        "step_efficiency": taken / needed,
        "modes":           sorted(modes, key=MODE_NAMES.index),
    }


# ═════════════════════════════════════════════════════════════════════════════
# 4. AGGREGATES
# ═════════════════════════════════════════════════════════════════════════════

def load_runs(variant: str) -> list:
    p = traces_path(variant)
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def aggregate(runs: list) -> dict:
    scored = [score_run(r) for r in runs]
    n = len(scored)
    costs  = [r["cost_usd"] for r in runs]
    toks   = [r["total_tokens"] for r in runs]
    lats   = [r["latency_s"] for r in runs]
    effs   = [s["step_efficiency"] for s in scored]
    o = sum(s["outcome_pass"] for s in scored) / n
    t = sum(s["trajectory_pass"] for s in scored) / n
    return {
        "n_runs":              n,
        "outcome_pass_rate":   o,
        "trajectory_pass_rate": t,
        "gap":                 o - t,
        "tool_choice_accuracy": sum(s["tool_choice_correct"] for s in scored)
                                / sum(s["tool_choice_total"] for s in scored),
        "arg_validity":        sum(s["args_valid"] for s in scored)
                                / sum(s["args_total"] for s in scored),
        "args_valid":          sum(s["args_valid"] for s in scored),
        "args_total":          sum(s["args_total"] for s in scored),
        "step_eff_mean":       statistics.mean(effs),
        "step_eff_p50":        statistics.median(effs),
        "step_eff_max":        max(effs),
        "step_eff_min":        min(effs),
        "steps_taken":         sum(s["steps_taken"] for s in scored),
        "steps_needed":        sum(s["steps_needed"] for s in scored),
        "cost_p50":            statistics.median(costs),
        "cost_max":            max(costs),
        "cost_mean":           statistics.mean(costs),
        "tokens_p50":          statistics.median(toks),
        "tokens_max":          max(toks),
        "tokens_mean":         statistics.mean(toks),
        "latency_p50":         statistics.median(lats),
        "latency_max":         max(lats),
        "latency_mean":        statistics.mean(lats),
        "llm_calls_mean":      statistics.mean(r["llm_calls"] for r in runs),
        "mode_counts":         {m: sum(m in s["modes"] for s in scored) for m in MODE_NAMES},
        "quadrants": {
            "outcome_pass_traj_pass": sum(s["outcome_pass"] and s["trajectory_pass"] for s in scored),
            "outcome_pass_traj_fail": sum(s["outcome_pass"] and not s["trajectory_pass"] for s in scored),
            "outcome_fail_traj_pass": sum(not s["outcome_pass"] and s["trajectory_pass"] for s in scored),
            "outcome_fail_traj_fail": sum(not s["outcome_pass"] and not s["trajectory_pass"] for s in scored),
        },
        "scored": scored,
    }


# ═════════════════════════════════════════════════════════════════════════════
# 5. SELF-TEST — the expected sequences, asserted in code
# ═════════════════════════════════════════════════════════════════════════════

def _synthetic_run(cid: str, sigs: tuple, verdict=None, payable=None, excl=None) -> dict:
    case = CLAIMS[cid]
    traj = []
    for sig in sigs:
        if sig == C:
            traj.append({"tool": "get_claim", "args": {"claim_id": cid}, "result": "{}"})
        elif sig.startswith("notes:"):
            traj.append({"tool": "get_adjuster_notes",
                         "args": {"claim_id": cid, "status": sig[6:]}, "result": "{}"})
        elif sig.startswith("excl:"):
            cause = sig[5:]
            row = POLICY_DB[cause]
            traj.append({"tool": "search_policy_exclusions", "args": {"cause": cause},
                         "result": json.dumps({"exclusions": row["exclusions"],
                                               "description": row["description"]})})
        elif sig == S:
            traj.append({"tool": "submit_verdict", "args": {
                "verdict": verdict or case["expected_verdict"],
                "payable_amount": case["expected_payable"] if payable is None else payable,
                "reason": "x", "exclusion_applied": excl}, "result": "{}"})
    v = traj[-1]["args"] if traj and traj[-1]["tool"] == "submit_verdict" else None
    return {"claim_id": cid, "trajectory": traj, "budget_hit": None,
            "verdict": v["verdict"] if v else "no_verdict",
            "payable_amount": v["payable_amount"] if v else 0.0}


def selftest():
    # every accepted path, with the right answer, passes both evals
    n = 0
    for cid, paths in EXPECTED.items():
        for p in paths:
            s = score_run(_synthetic_run(cid, p))
            assert s["trajectory_pass"] and s["outcome_pass"] and not s["modes"], (cid, p, s)
            n += 1
    print(f"  ok  {n} accepted sequences across 10 cases all score as PASS")

    bad = [
        # (case, sequence, kwargs, expected mode)
        ("CLM-002", (C, NR, "excl:arson", S), {"excl": "arson_and_criminal_acts"},
         "skipped_required_step"),                 # denied on unconfirmed inquiry
        ("CLM-007", (C, NR, S), {}, "skipped_required_step"),   # never opened exclusions
        ("CLM-001", (C, NR, "excl:water_damage", S), {"verdict": "denied", "payable": 0.0},
         "wrong_argument_choice"),                 # anchored on declared type
        ("CLM-004", (C, NR, "excl:theft", S), {"excl": "theft_exclusion_7"},
         "invalid_argument"),                      # fluent fiction
        ("CLM-001", (C, NR, S), {"excl": "all_flood_damage"},
         "invalid_argument"),                      # real id, never looked up
        ("CLM-005", (C, NR, NR, "excl:hail_damage", S), {}, "repeated_call"),
        ("CLM-005", (C, NR, "notes:pending", "excl:hail_damage", S), {}, "off_path_call"),
        ("CLM-001", (C, "excl:flood", NR, S), {}, "ordering_violation"),  # guessed cause
        ("CLM-006", (C, NR), {}, "no_verdict"),
        ("CLM-007", (C, NR, "excl:water_damage", S), {"verdict": "approved", "payable": 9000.0},
         "wrong_outcome_despite_evidence"),
    ]
    for cid, seq, kw, mode in bad:
        s = score_run(_synthetic_run(cid, seq, **kw))
        assert mode in s["modes"], (cid, seq, mode, s["modes"])
        if mode != "wrong_outcome_despite_evidence":
            assert not s["trajectory_pass"], (cid, seq)
        print(f"  ok  {cid} {' > '.join(seq):<58} -> {mode}")

    # the brief's headline case: clean claim, right payout, exclusions never opened
    s = score_run(_synthetic_run("CLM-007", (C, NR, S)))
    assert s["outcome_pass"] and not s["trajectory_pass"]
    print("  ok  right-answer-wrong-path is outcome PASS / trajectory FAIL")
    print("selftest passed")


# ═════════════════════════════════════════════════════════════════════════════
# 6. RUNNER
# ═════════════════════════════════════════════════════════════════════════════

TPM_LIMIT = 8000   # Groq tokens-per-minute limit on this key


def run(variant: str, repeats: int, only=None):
    from app.config import get_groq_api_key, get_groq_model
    from week8.agent import run_agent_traced

    api_key, model = get_groq_api_key(), get_groq_model()
    out = traces_path(variant)
    ids = only or list(EXPECTED)
    print(f"Running {variant} agent: {len(ids)} claims x {repeats} repeats, model={model}")
    print(f"Writing {out.relative_to(ROOT)}")
    with open(out, "w") as f:
        for rep in range(1, repeats + 1):
            for cid in ids:
                r = run_agent_traced(cid, api_key, model, variant=variant)
                r["repeat"] = rep
                r["model"]  = model
                f.write(json.dumps(r) + "\n")
                f.flush()
                s = score_run(r)
                print(f"  r{rep} {cid}  outcome={'PASS' if s['outcome_pass'] else 'FAIL'}  "
                      f"traj={'PASS' if s['trajectory_pass'] else 'FAIL'}  "
                      f"{' > '.join(s['sequence'])}  "
                      f"${r['cost_usd']:.6f}  {r['latency_s']:.1f}s  {s['modes'] or ''}")
                # pace to the TPM limit so 429 back-off doesn't dominate
                time.sleep(max(0.0, r["total_tokens"] * 60 / TPM_LIMIT - r["latency_s"]))


# ═════════════════════════════════════════════════════════════════════════════
# 7. REPORT
# ═════════════════════════════════════════════════════════════════════════════

def _pct(x):  return f"{100 * x:.1f}%"
def _usd(x):  return f"${x:.6f}"


def _trace_block(run: dict, s: dict) -> str:
    lines = []
    for i, st in enumerate(run["trajectory"], 1):
        res = st["result"]
        if st["tool"] == "get_adjuster_notes":
            try:
                res = json.loads(res).get("notes", res)
            except Exception:
                pass
        lines.append(f"{i}. {st['tool']}({st['args_raw'] if 'args_raw' in st else json.dumps(st['args'])})")
        if st["tool"] != "submit_verdict":
            lines.append(f"     -> {res[:260]}")
    return "\n".join(lines)


def report():
    base = load_runs("baseline")
    mit  = load_runs("mitigated")
    if not base:
        sys.exit("No baseline traces — run:  python3 week8/trajectory_eval.py run --variant baseline")
    A = aggregate(base)
    B = aggregate(mit) if mit else None

    md = []
    md.append("# Week 8 — Trajectory Eval Results (auto-generated)\n")
    md.append(f"Generated by `python3 week8/trajectory_eval.py report` from "
              f"`traces_baseline.jsonl`" + (" and `traces_mitigated.jsonl`" if B else "") + ".  ")
    md.append(f"Model: `{base[0].get('model', '?')}` · temperature 0 · "
              f"{A['n_runs']} runs per variant (10 claims × {A['n_runs'] // 10} repeats).  ")
    md.append("Cost uses the week7 pricing constants ($0.90 / 1M input and output tokens). "
              "Latency excludes 429 rate-limit back-off.\n")

    # ── expected sequences
    md.append("## 1. Expected tool sequences (asserted in `EXPECTED`)\n")
    md.append("| Claim | Type | Steps needed | Accepted paths | Required in every path |")
    md.append("|---|---|---|---|---|")
    for cid in EXPECTED:
        c = CLAIMS[cid]
        kind = "conditional" if c["is_conditional"] else "clean"
        paths = "<br>".join(" > ".join(p) for p in sorted(EXPECTED[cid], key=lambda p: (len(p), p)))
        req = ", ".join(sorted(required_steps(cid)))
        md.append(f"| {cid} | {kind} ({c['declared_type']}) | {steps_needed(cid)} | "
                  f"{len(EXPECTED[cid])} alternates:<br>{paths} | {req} |")
    md.append("")

    # ── results table
    def col(X, key, fmt):
        return fmt(X[key]) if X else "—"
    md.append("## 2. Trajectory results\n")
    md.append("| Metric | Baseline | Mitigated |")
    md.append("|---|---|---|")
    rows = [
        ("Runs", "n_runs", str),
        ("**Tool-choice accuracy**", "tool_choice_accuracy", _pct),
        ("**Argument validity rate**", "arg_validity", _pct),
        ("**Step efficiency** (taken / needed), mean", "step_eff_mean", lambda x: f"{x:.3f}"),
        ("Step efficiency p50 / min / max", None, None),
        ("**Cost per claim p50**", "cost_p50", _usd),
        ("**Cost per claim max**", "cost_max", _usd),
        ("Cost per claim mean (reference only)", "cost_mean", _usd),
        ("Tokens per claim p50 / max", None, None),
        ("Latency per claim p50 / max (s)", None, None),
        ("LLM calls per claim, mean", "llm_calls_mean", lambda x: f"{x:.2f}"),
        ("Outcome pass rate", "outcome_pass_rate", _pct),
        ("Trajectory pass rate", "trajectory_pass_rate", _pct),
        ("**Gap (outcome − trajectory)**", "gap", lambda x: f"{100 * x:+.1f} pp"),
    ]
    for label, key, fmt in rows:
        if key:
            md.append(f"| {label} | {col(A, key, fmt)} | {col(B, key, fmt)} |")
        elif label.startswith("Step"):
            f = lambda X: f"{X['step_eff_p50']:.2f} / {X['step_eff_min']:.2f} / {X['step_eff_max']:.2f}" if X else "—"
            md.append(f"| {label} | {f(A)} | {f(B)} |")
        elif label.startswith("Tokens"):
            f = lambda X: f"{X['tokens_p50']:.0f} / {X['tokens_max']}" if X else "—"
            md.append(f"| {label} | {f(A)} | {f(B)} |")
        else:
            f = lambda X: f"{X['latency_p50']:.2f} / {X['latency_max']:.2f}" if X else "—"
            md.append(f"| {label} | {f(A)} | {f(B)} |")
    md.append("")
    md.append(f"Argument validity detail: baseline {A['args_valid']}/{A['args_total']} arguments valid"
              + (f"; mitigated {B['args_valid']}/{B['args_total']}." if B else "."))
    md.append("")
    md.append("Definitions: *tool-choice accuracy* = LCS between the actual call signatures and the "
              "closest accepted path ÷ max(len(actual), len(path)), pooled over runs (penalises "
              "wrong, missing and extra calls). *Argument validity* = checked arguments that are "
              "real and grounded ÷ all checked arguments (claim_id exists and is this claim; status "
              "and cause are real enums; exclusion_applied is null or an exclusion id/clause actually "
              "returned by a lookup in that run). *Step efficiency* = tool calls taken ÷ length of "
              "the shortest accepted path; >1 is wasted steps, <1 means required steps were skipped.\n")

    # ── per-case table
    for name, X in (("Baseline", A), ("Mitigated", B)):
        if not X:
            continue
        md.append(f"### Per-claim — {name}\n")
        md.append("| Claim | Outcome pass | Trajectory pass | Paths taken (count) |")
        md.append("|---|---|---|---|")
        for cid in EXPECTED:
            ss = [s for s in X["scored"] if s["claim_id"] == cid]
            paths = Counter(" > ".join(s["sequence"]) for s in ss)
            md.append(f"| {cid} | {sum(s['outcome_pass'] for s in ss)}/{len(ss)} | "
                      f"{sum(s['trajectory_pass'] for s in ss)}/{len(ss)} | "
                      + "<br>".join(f"`{p}` ×{k}" for p, k in paths.most_common()) + " |")
        md.append("")

    # ── gap
    q = A["quadrants"]
    md.append("## 3. Outcome-vs-trajectory gap (baseline)\n")
    md.append(f"**Gap = {_pct(A['outcome_pass_rate'])} − {_pct(A['trajectory_pass_rate'])} "
              f"= {100 * A['gap']:+.1f} percentage points**\n")
    md.append("| | Trajectory PASS | Trajectory FAIL |")
    md.append("|---|---|---|")
    md.append(f"| **Outcome PASS** | {q['outcome_pass_traj_pass']} | "
              f"{q['outcome_pass_traj_fail']} ← right answer, wrong path |")
    md.append(f"| **Outcome FAIL** | {q['outcome_fail_traj_pass']} | {q['outcome_fail_traj_fail']} |")
    md.append("")
    raws = [(r, s) for r, s in zip(base, A["scored"])
            if s["outcome_pass"] and not s["trajectory_pass"]]
    if raws:
        # prefer the case where skipped evidence matters most (CLM-002), else first
        raws.sort(key=lambda rs: (rs[0]["claim_id"] != "CLM-002", rs[0].get("repeat", 0)))
        r, s = raws[0]
        md.append(f"### Right answer, wrong path — {r['claim_id']} (repeat {r.get('repeat')})\n")
        md.append(f"Outcome: verdict `{r['verdict']}`, payable £{r['payable_amount']:.2f} "
                  f"(expected `{CLAIMS[r['claim_id']]['expected_verdict']}` / "
                  f"£{CLAIMS[r['claim_id']]['expected_payable']:.2f}) → **outcome PASS**.  ")
        md.append(f"Path taken: `{' > '.join(s['sequence'])}`  ")
        md.append(f"Closest accepted path: `{' > '.join(s['closest_expected'])}`  ")
        md.append(f"Missing: `{s['missing']}` · Extra: `{s['extra']}` · Modes: `{s['modes']}` "
                  f"→ **trajectory FAIL**\n")
        md.append("```text\n" + _trace_block(r, s) + "\n```\n")
        others = Counter(rs[0]["claim_id"] for rs in raws)
        md.append("All right-answer-wrong-path runs: "
                  + ", ".join(f"{k} ×{v}" for k, v in sorted(others.items())) + "\n")

    # ── modes
    md.append("## 4. Failure modes — per-mode counts (runs exhibiting the mode)\n")
    if B:
        md.append("| Mode | Baseline | Mitigated | Δ | Status |")
        md.append("|---|---|---|---|---|")
        for m, desc in MODES:
            a, b = A["mode_counts"][m], B["mode_counts"][m]
            status = ("improved" if b < a else "**WORSE**" if b > a and a > 0
                      else "**NEW — appeared after mitigation**" if b > a else "unchanged")
            md.append(f"| `{m}` | {a} | {b} | {b - a:+d} | {status} |")
    else:
        md.append("| Mode | Baseline | Description |")
        md.append("|---|---|---|")
        for m, desc in MODES:
            md.append(f"| `{m}` | {A['mode_counts'][m]} | {desc} |")
    md.append("")

    if B:
        md.append("## 5. Price of the mitigation (baseline → mitigated)\n")
        md.append("| Cost dimension | Baseline | Mitigated | Δ |")
        md.append("|---|---|---|---|")
        for label, key, fmt in [
            ("Cost/claim p50", "cost_p50", _usd), ("Cost/claim max", "cost_max", _usd),
            ("Cost/claim mean", "cost_mean", _usd),
            ("Tokens/claim p50", "tokens_p50", lambda x: f"{x:.0f}"),
            ("Tokens/claim max", "tokens_max", lambda x: f"{x:.0f}"),
            ("Latency/claim p50 (s)", "latency_p50", lambda x: f"{x:.2f}"),
            ("Latency/claim max (s)", "latency_max", lambda x: f"{x:.2f}"),
            ("LLM calls/claim mean", "llm_calls_mean", lambda x: f"{x:.2f}"),
            ("Tool calls total", "steps_taken", str),
        ]:
            a, b = A[key], B[key]
            rel = f" ({100 * (b - a) / a:+.1f}%)" if a else ""
            md.append(f"| {label} | {fmt(a)} | {fmt(b)} | {fmt(b - a) if key != 'steps_taken' else f'{b - a:+d}'}{rel} |")
        md.append("")

    RESULTS_MD.write_text("\n".join(md))
    print("\n".join(md))
    print(f"\nWrote {RESULTS_MD.relative_to(ROOT)}")


# ═════════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("selftest")
    r = sub.add_parser("run")
    r.add_argument("--variant", choices=["baseline", "mitigated"], required=True)
    r.add_argument("--repeats", type=int, default=3)
    r.add_argument("--only", nargs="*")
    sub.add_parser("report")
    a = ap.parse_args()
    if a.cmd == "selftest":
        selftest()
    elif a.cmd == "run":
        selftest()
        run(a.variant, a.repeats, a.only)
    else:
        report()


if __name__ == "__main__":
    main()
