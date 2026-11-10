"""
Week 8 — Traced claims agent.

Same loop, system prompt, tools, budgets and pricing as week7/agent.py. The
only difference is instrumentation: every tool call is recorded as a structured
step (tool name, parsed arguments, raw result) so the trajectory can be scored,
not just the final verdict.

Two defensive changes vs week7, neither of which changes behaviour on a normal
run (they only matter on paths that would have crashed week7 mid-eval):
  - malformed JSON tool arguments are recorded and fed back as a tool error
  - a tool call with wrong keyword arguments is fed back as a tool error

The `variant` argument selects which tool schemas the agent sees:
  "baseline"  — week7 TOOL_SCHEMAS, unchanged
  "mitigated" — the single Week 8 mitigation (see week8/mitigation.py)

Usage:
    python3 week8/agent.py CLM-002
    python3 week8/agent.py CLM-002 --mitigated
"""

import json
import sys
import time
import pathlib
import urllib.request
import urllib.error

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_groq_api_key, get_groq_model, load_env_file
from week7.agent import (
    SYSTEM_PROMPT, SUBMIT_VERDICT_SCHEMA, GROQ_URL,
    MAX_ITERS, MAX_TOKENS, MAX_COST_USD, WALL_CLOCK_S, _cost,
)
from week7.tools import TOOL_SCHEMAS, dispatch_tool
from week8.mitigation import MITIGATED_TOOL_SCHEMAS

load_env_file()

VARIANTS = {
    "baseline":  TOOL_SCHEMAS + [SUBMIT_VERDICT_SCHEMA],
    "mitigated": MITIGATED_TOOL_SCHEMAS + [SUBMIT_VERDICT_SCHEMA],
}


def _groq_call(messages: list, api_key: str, model: str, tools: list):
    """
    Single Groq API call with retry on 429.
    Returns (response_json, seconds_spent_sleeping_on_429) so rate-limit
    back-off can be excluded from the latency we report as the agent's cost.
    """
    payload = {
        "model":       model,
        "messages":    messages,
        "temperature": 0.0,
        "tools":       tools,
        "tool_choice": "auto",
    }
    slept = 0.0
    for attempt in range(6):
        try:
            req = urllib.request.Request(
                GROQ_URL,
                data=json.dumps(payload).encode(),
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type":  "application/json",
                    "User-Agent":    "ClaimsAgent/1.0",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read()), slept
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="ignore")
            if e.code in (429, 500, 502, 503) and attempt < 5:
                delay = (2 ** attempt) + 1.5
                time.sleep(delay)
                slept += delay
                continue
            raise RuntimeError(f"Groq HTTP {e.code}: {body}")
    raise RuntimeError("Groq: max retries exceeded")


def run_agent_traced(
    claim_id: str,
    api_key:  str,
    model:    str,
    variant:  str = "baseline",
    budgets=None,
) -> dict:
    """
    Run the claims agent on one claim and return the outcome plus the full
    trajectory.

    trajectory is a list of steps, one per tool call, in call order:
        {"iter": int, "tool": str, "args": dict | None,
         "args_raw": str, "args_parse_error": bool, "result": str}
    submit_verdict is recorded as a step too (it is the agent's final action).
    """
    tools = VARIANTS[variant]
    b = {
        "max_iters":    MAX_ITERS,
        "max_tokens":   MAX_TOKENS,
        "max_cost_usd": MAX_COST_USD,
        "wall_clock_s": WALL_CLOCK_S,
    }
    if budgets:
        b.update(budgets)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": f"Triage claim {claim_id}."},
    ]

    t_start          = time.perf_counter()
    slept_total      = 0.0
    total_prompt     = 0
    total_completion = 0
    total_cost       = 0.0
    llm_calls        = 0
    budget_hit       = None
    verdict_raw      = None
    trajectory       = []

    while True:
        # wall clock excludes 429 back-off so a rate-limited run is not
        # mistaken for a slow agent
        elapsed = time.perf_counter() - t_start - slept_total

        # ── Budget checks (identical to week7) ────────────────────────────────
        if llm_calls >= b["max_iters"]:
            budget_hit = "max_iters"
            break
        if total_prompt + total_completion > b["max_tokens"]:
            budget_hit = "max_tokens"
            break
        if total_cost > b["max_cost_usd"]:
            budget_hit = "max_cost"
            break
        if elapsed > b["wall_clock_s"]:
            budget_hit = "wall_clock"
            break

        resp, slept = _groq_call(messages, api_key, model, tools)
        slept_total += slept
        llm_calls   += 1
        usage = resp.get("usage", {})
        pt    = usage.get("prompt_tokens", 0)
        ct    = usage.get("completion_tokens", 0)
        total_prompt     += pt
        total_completion += ct
        total_cost       += _cost(pt, ct)

        msg        = resp["choices"][0]["message"]
        tool_calls = msg.get("tool_calls") or []
        messages.append(msg)

        if not tool_calls:
            # Text answer with no tool call — no verdict was submitted.
            trajectory.append({
                "iter": llm_calls, "tool": "<text>", "args": None,
                "args_raw": (msg.get("content") or "")[:500],
                "args_parse_error": False, "result": "",
            })
            break

        final_answer = False
        for tc in tool_calls:
            fn_name  = tc["function"]["name"]
            args_raw = tc["function"].get("arguments") or "{}"
            try:
                fn_args   = json.loads(args_raw)
                parse_err = not isinstance(fn_args, dict)
            except Exception:
                fn_args, parse_err = None, True

            if parse_err:
                result = json.dumps({"error": "Tool arguments were not valid JSON."})
            elif fn_name == "submit_verdict":
                verdict_raw  = fn_args
                final_answer = True
                result = json.dumps({"status": "recorded"})
            else:
                try:
                    result = dispatch_tool(fn_name, fn_args)
                except TypeError as e:
                    result = json.dumps({"error": f"Bad arguments: {e}"})

            trajectory.append({
                "iter": llm_calls, "tool": fn_name, "args": fn_args,
                "args_raw": args_raw, "args_parse_error": parse_err,
                "result": result,
            })
            messages.append({
                "role":         "tool",
                "tool_call_id": tc["id"],
                "content":      result,
            })

        if final_answer:
            break

    wall    = time.perf_counter() - t_start
    latency = wall - slept_total
    v = verdict_raw or {}
    try:
        payable = float(v.get("payable_amount", 0.0) or 0.0)
    except (TypeError, ValueError):
        payable = 0.0

    return {
        "claim_id":          claim_id,
        "variant":           variant,
        "verdict":           v.get("verdict", "no_verdict") if verdict_raw else "no_verdict",
        "payable_amount":    payable,
        "reason":            v.get("reason", ""),
        "exclusion_applied": v.get("exclusion_applied"),
        "llm_calls":         llm_calls,
        "tool_calls":        len(trajectory),
        "prompt_tokens":     total_prompt,
        "completion_tokens": total_completion,
        "total_tokens":      total_prompt + total_completion,
        "cost_usd":          round(total_cost, 6),
        "latency_s":         round(latency, 3),
        "rate_limit_sleep_s": round(slept_total, 2),
        "budget_hit":        budget_hit,
        "trajectory":        trajectory,
    }


def _main():
    args    = sys.argv[1:]
    variant = "mitigated" if "--mitigated" in args else "baseline"
    ids     = [a for a in args if not a.startswith("--")] or ["CLM-001"]
    api_key, model = get_groq_api_key(), get_groq_model()
    for cid in ids:
        r = run_agent_traced(cid, api_key, model, variant=variant)
        print(f"\n{cid} [{variant}]  verdict={r['verdict']}  "
              f"payable={r['payable_amount']}  excl={r['exclusion_applied']}  "
              f"tokens={r['total_tokens']}  cost=${r['cost_usd']:.6f}  "
              f"latency={r['latency_s']}s  budget_hit={r['budget_hit']}")
        for s in r["trajectory"]:
            print(f"  iter {s['iter']}: {s['tool']}({s['args_raw']})")


if __name__ == "__main__":
    _main()
