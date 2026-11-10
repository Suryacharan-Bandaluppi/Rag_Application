"""
Week 11 — what breaks first at 10x today's claim volume?

Inputs (all measured, none assumed):
  - yesterday's production traffic (week11/logs/traces.jsonl, 2026-10-26):
    requests, LLM calls per request, tokens, cost, latency, peak minute
  - the account's live rate limits, read from Groq's response headers on a
    1-token request: x-ratelimit-limit-requests (per day) and
    x-ratelimit-limit-tokens (per minute)

For each candidate (cost, latency, a rate limit) it computes the value at 10x
and the multiplier at which that limit is hit; the smallest multiplier breaks
first.

    python3 week11/capacity.py [--date 2026-10-26]
"""

import argparse
import json
import math
import pathlib
import statistics
import sys
import urllib.request
from collections import Counter

WEEK11 = pathlib.Path(__file__).resolve().parent
ROOT   = WEEK11.parent
sys.path.insert(0, str(ROOT))

from app.config import get_groq_api_key, get_groq_model

LOG = WEEK11 / "logs" / "traces.jsonl"


def live_limits():
    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=json.dumps({"model": get_groq_model(), "max_tokens": 1,
                         "messages": [{"role": "user", "content": "ok"}]}).encode(),
        headers={"Authorization": f"Bearer {get_groq_api_key()}",
                 "Content-Type": "application/json", "User-Agent": "CoverageAssistant/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        h = {k.lower(): v for k, v in r.headers.items()}
    return {"requests_per_day": int(h["x-ratelimit-limit-requests"]),
            "tokens_per_minute": int(h["x-ratelimit-limit-tokens"])}


def p(values, q):
    s = sorted(values)
    return s[max(0, math.ceil(q / 100 * len(s)) - 1)]


def llm_calls(trace):
    return sum(1 for s in trace["spans"] if s["span"].startswith("llm."))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2026-10-26")
    a = ap.parse_args()
    rows = [json.loads(l) for l in LOG.read_text().splitlines() if l.strip()]
    day = [r for r in rows if r["env"] == "prod" and r["ts"].startswith(a.date)]
    lim = live_limits()

    n        = len(day)
    calls    = sum(llm_calls(r) for r in day)
    tokens   = sum(r["tokens"] for r in day)
    cost     = sum(r["cost_usd"] for r in day)
    per_min  = Counter()
    for r in day:
        per_min[r["ts"][:16]] += r["tokens"]
    peak_min_key, peak_min_tok = per_min.most_common(1)[0]
    per_hour = Counter()
    for r in day:
        per_hour[r["ts"][:13]] += r["tokens"]
    peak_hour_key, peak_hour_tok = per_hour.most_common(1)[0]
    peak_hour_tpm = peak_hour_tok / 60
    lat = [r["latency_ms"] / 1000 for r in day]

    rpd_x = lim["requests_per_day"] / calls            # multiplier that hits the daily request cap
    tpm_x = lim["tokens_per_minute"] / peak_min_tok    # multiplier that hits the per-minute token cap
    out = {
        "date": a.date, "requests": n, "llm_calls": calls,
        "llm_calls_per_request": round(calls / n, 2),
        "tokens": tokens, "cost_usd": round(cost, 6),
        "cost_per_request_usd": round(cost / n, 7),
        "peak_minute": peak_min_key, "peak_minute_tokens": peak_min_tok,
        "busiest_hour": peak_hour_key, "busiest_hour_avg_tokens_per_minute": round(peak_hour_tpm, 1),
        "latency_p50_s": round(statistics.median(lat), 2), "latency_p99_s": round(p(lat, 99), 2),
        "limits": lim,
        "at_10x": {
            "llm_calls_per_day": calls * 10,
            "peak_minute_tokens": peak_min_tok * 10,
            "busiest_hour_avg_tokens_per_minute": round(peak_hour_tpm * 10, 1),
            "cost_per_day_usd": round(cost * 10, 4),
        },
        "breaks_at_multiplier": {
            "requests_per_day_limit": round(rpd_x, 2),
            "tokens_per_minute_limit_at_peak_minute": round(tpm_x, 2),
            "tokens_per_minute_limit_at_busiest_hour_avg": round(lim["tokens_per_minute"] / peak_hour_tpm, 2),
        },
    }
    print(json.dumps(out, indent=2))
    (WEEK11 / "evidence").mkdir(exist_ok=True)
    (WEEK11 / "evidence" / "capacity.json").write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
