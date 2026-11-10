"""
Week 11 — cost per query broken down by stage, from the trace log.

    python3 week11/cost_report.py --trace tr_xxx [--date 2026-10-26]

Prints (and writes week11/cost_by_stage.md) the per-stage cost, tokens and
latency of one request, next to the per-stage average over that day's
production traffic for comparison.
"""

import argparse
import json
import pathlib
import statistics
from collections import defaultdict

WEEK11 = pathlib.Path(__file__).resolve().parent
LOG    = WEEK11 / "logs" / "traces.jsonl"
OUT    = WEEK11 / "cost_by_stage.md"
STAGES = ["retrieval", "tools", "generation"]


def by_stage(trace):
    agg = {s: {"cost": 0.0, "tokens": 0, "latency_ms": 0.0, "spans": []} for s in STAGES}
    for sp in trace["spans"]:
        a = agg[sp["stage"]]
        a["cost"] += sp["cost_usd"]
        a["tokens"] += sp["tokens"]
        a["latency_ms"] += sp["latency_ms"]
        a["spans"].append(sp["span"])
    return agg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trace", required=True)
    ap.add_argument("--date", default="2026-10-26")
    ap.add_argument("--log", default=str(LOG))
    a = ap.parse_args()
    rows = [json.loads(l) for l in pathlib.Path(a.log).read_text().splitlines() if l.strip()]
    day = [r for r in rows if r["env"] == "prod" and r["ts"].startswith(a.date)]
    t = next(r for r in rows if r["trace_id"] == a.trace)
    one = by_stage(t)
    total = sum(v["cost"] for v in one.values())

    avg = defaultdict(list)
    for r in day:
        for s, v in by_stage(r).items():
            avg[s].append(v["cost"])
    day_total = statistics.mean(r["cost_usd"] for r in day)

    L = [f"# cost_by_stage.md — cost per query by stage\n",
         f"Request **{t['trace_id']}** ({t['ts']}, {t['input_type']}, prompt `{t['prompt_version']}`, "
         f"retrieval `{t['retrieval_version']}`, model `{t['model']}`). Pricing: $0.90 per 1M tokens "
         "in and out (same constant as weeks 7–10). Retrieval is a local BM25 index, so it uses no "
         "model tokens and costs $0 in API spend.\n",
         "| Stage | Spans | Tokens | Latency | Cost (USD) | Share of cost |",
         "|---|---|---|---|---|---|"]
    for s in STAGES:
        v = one[s]
        share = 100 * v["cost"] / total if total else 0
        L.append(f"| {s} | {', '.join(v['spans']) or '—'} | {v['tokens']:,} | {v['latency_ms']:.0f} ms | "
                 f"${v['cost']:.7f} | {share:.1f}% |")
    L.append(f"| **total** | | **{t['tokens']:,}** | **{t['latency_ms']:.0f} ms** | **${total:.7f}** | 100% |")
    L += ["", f"For comparison, mean per query over all {len(day)} production requests on {a.date}:\n",
          "| Stage | Mean cost (USD) | Share |", "|---|---|---|"]
    for s in STAGES:
        m = statistics.mean(avg[s]) if avg[s] else 0.0
        L.append(f"| {s} | ${m:.7f} | {100 * m / day_total:.1f}% |")
    L.append(f"| **total** | **${day_total:.7f}** | 100% |")
    OUT.write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
