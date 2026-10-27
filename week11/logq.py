"""
Week 11 — query the trace log (the only tool used in the drill).

Slices:  --date --from --to (time)   --user   --prompt-version   --input-type
         --cost-outlier N (top N by cost, with z-score)
Output:  --decision   --text REGEX (searches query AND answer; --in to narrow)
Context: --context-has ID   --context-missing ID   --cited ID
Views:   --facets (counts per slice)   --show TRACE_ID [--json]

Examples:
    python3 week11/logq.py --date 2026-10-26 --facets
    python3 week11/logq.py --date 2026-10-26 --decision covered --text "drink|alcohol"
    python3 week11/logq.py --show tr_ab12cd34ef56 --json > week11/trace.json
"""

import argparse
import json
import pathlib
import re
import statistics
import sys
from collections import Counter

WEEK11 = pathlib.Path(__file__).resolve().parent
DEFAULT_LOG = WEEK11 / "logs" / "traces.jsonl"


def load(path):
    return [json.loads(l) for l in pathlib.Path(path).read_text().splitlines() if l.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=str(DEFAULT_LOG))
    ap.add_argument("--env", default="prod")
    ap.add_argument("--date")
    ap.add_argument("--from", dest="t_from")
    ap.add_argument("--to", dest="t_to")
    ap.add_argument("--user")
    ap.add_argument("--prompt-version")
    ap.add_argument("--input-type")
    ap.add_argument("--decision")
    ap.add_argument("--text")
    ap.add_argument("--in", dest="text_in", choices=["query", "answer", "both"], default="both")
    ap.add_argument("--context-has")
    ap.add_argument("--context-missing")
    ap.add_argument("--cited")
    ap.add_argument("--cost-outlier", type=int)
    ap.add_argument("--facets", action="store_true")
    ap.add_argument("--show")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--full", action="store_true", help="do not truncate query/answer")
    a = ap.parse_args()

    rows = [r for r in load(a.log) if a.env in (None, "all") or r.get("env") == a.env]

    if a.show:
        r = next((x for x in rows if x["trace_id"] == a.show), None)
        if r is None:
            sys.exit(f"no trace {a.show}")
        print(json.dumps(r, indent=2))
        return

    def keep(r):
        ts = r["ts"]
        if a.date and not ts.startswith(a.date):
            return False
        hhmm = ts[11:16]
        if a.t_from and hhmm < a.t_from:
            return False
        if a.t_to and hhmm > a.t_to:
            return False
        if a.user and r["user_id"] != a.user:
            return False
        if a.prompt_version and r["prompt_version"] != a.prompt_version:
            return False
        if a.input_type and r["input_type"] != a.input_type:
            return False
        if a.decision and r.get("decision") != a.decision:
            return False
        if a.text:
            hay = {"query": r.get("query_redacted") or "",
                   "answer": r.get("answer_redacted") or ""}
            fields = [hay["query"], hay["answer"]] if a.text_in == "both" else [hay[a.text_in]]
            if not any(re.search(a.text, f, re.I) for f in fields):
                return False
        ctx = r.get("context_ids") or []
        if a.context_has and a.context_has not in ctx:
            return False
        if a.context_missing and a.context_missing in ctx:
            return False
        if a.cited and a.cited not in (r.get("clauses_cited") or []):
            return False
        return True

    hits = [r for r in rows if keep(r)]

    if a.cost_outlier:
        costs = [r["cost_usd"] for r in hits]
        mu, sd = statistics.mean(costs), statistics.pstdev(costs) or 1e-12
        for r in hits:
            r["_z"] = (r["cost_usd"] - mu) / sd
        hits = sorted(hits, key=lambda r: -r["cost_usd"])[: a.cost_outlier]
        print(f"cost mean=${mu:.6f} sd=${sd:.6f}")

    if a.facets:
        print(f"{len(hits)} traces")
        for key in ("user_id", "prompt_version", "input_type", "decision"):
            c = Counter(r.get(key) for r in hits)
            print(f"  {key:<15} " + "  ".join(f"{k}={v}" for k, v in c.most_common()))
        c = Counter(r["ts"][11:13] + "h" for r in hits)
        print(f"  {'hour':<15} " + "  ".join(f"{k}={v}" for k, v in sorted(c.items())))
        return

    if a.json:
        print(json.dumps(hits, indent=2))
        return
    n = None if a.full else 110
    print(f"{len(hits)} matching traces")
    for r in hits:
        z = f" z={r['_z']:+.1f}" if "_z" in r else ""
        print(f"{r['ts']}  {r['trace_id']}  {r['user_id']:<7} {r['input_type']:<17} "
              f"{r['prompt_version']:<14} {str(r.get('decision')):<17} "
              f"ctx={','.join(r.get('context_ids') or [])}  cited={','.join(r.get('clauses_cited') or [])}  "
              f"${r['cost_usd']:.6f}{z}  {r['latency_ms']:.0f}ms")
        print(f"      Q: {(r.get('query_redacted') or '')[:n]}")
        print(f"      A: {(r.get('answer_redacted') or '')[:n]}")


if __name__ == "__main__":
    main()
