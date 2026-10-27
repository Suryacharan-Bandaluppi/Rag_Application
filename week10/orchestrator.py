"""
Week 10 — Claims ORCHESTRATOR (multi-agent arm).

The orchestrator is an LLM with four tools:
    get_claim                 read the claim record itself (one cheap read)
    ask_notes_worker          delegate: adjuster notes -> confirmed cause
    ask_exclusions_worker     delegate: cause -> exclusions / coverage
    submit_verdict            same output contract as the single agent

Workers are called over HTTP (week10/workers.py). The orchestrator then
synthesises the verdict using the SAME payout rules as the single agent's
prompt, so both arms are scored by the same judge.

Its prompt deliberately contains no special instructions for worker failures:
the race records what it actually does when a worker returns HTTP 500.

Every LLM call is logged as a hand-off with its token count:
    "user -> orchestrator"                orchestrator's first call
    "<tool or worker> -> orchestrator"    orchestrator call made after that result
                                          (re-sends the whole history)
    "orchestrator -> <worker>"            every LLM call inside that worker run
"""

import json
import pathlib
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_groq_api_key, get_groq_model, load_env_file
from week7.agent import SUBMIT_VERDICT_SCHEMA, _cost
from week7.tools import TOOL_SCHEMAS, VALID_CAUSES, dispatch_tool
from week8.agent import _groq_call

load_env_file()

ORCH_MAX_ITERS = 8

ORCH_PROMPT = """\
You are the claims orchestrator. You triage a claim by delegating to two
specialist workers and then synthesising their findings.

Steps:
1. Call get_claim to retrieve the claim record.
2. Call ask_notes_worker with the claim id. It returns the damage cause the
   adjuster notes CONFIRM, which may differ from the declared type.
3. Call ask_exclusions_worker with that ACTUAL cause (not the declared type)
   and the declared amount. It returns exclusions, payout_modifier, sub_limit
   and a coverage statement.
4. Compute the payable amount:
   - If payout_modifier is 0.0 -> verdict = "denied", payable_amount = 0.
   - If declared_amount exceeds sub_limit -> cap at sub_limit minus policy_excess, verdict = "partial".
   - Otherwise -> verdict = "approved", payable_amount = declared_amount - policy_excess.
5. Call submit_verdict with the result.

Rules:
- Apply ONLY the exclusions returned by the exclusions worker.
- Keep the worker's qualifiers: "covered subject to <limit or exclusion>" is
  not "covered". Put the exclusion or limit that drove the outcome in
  exclusion_applied and the reason.
- Your last action must be a call to submit_verdict.
"""

_SCHEMA = {t["function"]["name"]: t for t in TOOL_SCHEMAS}

ORCH_TOOLS = [
    _SCHEMA["get_claim"],
    {"type": "function", "function": {
        "name": "ask_notes_worker",
        "description": "Delegate to the adjuster-notes worker. Returns the damage cause "
                       "the adjuster notes confirm, whether it is confirmed, and a short summary.",
        "parameters": {"type": "object", "properties": {
            "claim_id": {"type": "string"}}, "required": ["claim_id"]},
    }},
    {"type": "function", "function": {
        "name": "ask_exclusions_worker",
        "description": "Delegate to the coverage/exclusions worker. Give it the ACTUAL damage "
                       "cause and the declared amount. Returns exclusions, payout_modifier, "
                       "sub_limit and a coverage statement.",
        "parameters": {"type": "object", "properties": {
            "cause":           {"type": "string", "enum": VALID_CAUSES},
            "declared_amount": {"type": "number"}},
            "required": ["cause", "declared_amount"]},
    }},
    SUBMIT_VERDICT_SCHEMA,
]

WORKER_OF = {"ask_notes_worker": "notes", "ask_exclusions_worker": "exclusions"}


def _post_worker(base_url: str, worker: str, claim_id: str, inp: dict) -> tuple:
    """POST to a worker. Returns (status_code, body_dict)."""
    req = urllib.request.Request(
        f"{base_url}/workers/{worker}",
        data=json.dumps({"claim_id": claim_id, "input": inp}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read() or b"{}")
        except json.JSONDecodeError:
            body = {"error": "unparseable error body"}
        return e.code, body


def run_orchestrator(claim_id: str, base_url: str) -> dict:
    api_key, model = get_groq_api_key(), get_groq_model()
    messages = [{"role": "system", "content": ORCH_PROMPT},
                {"role": "user",   "content": f"Triage claim {claim_id}."}]
    handoffs, events = [], []      # per-LLM-call token log; worker/tool events
    slept, verdict = 0.0, None
    trigger = "user -> orchestrator"
    t0 = time.perf_counter()

    def log_calls(label, agent, calls):
        for c in calls:
            handoffs.append({"handoff": label, "agent": agent,
                             "prompt_tokens": c["prompt_tokens"],
                             "completion_tokens": c["completion_tokens"],
                             "total_tokens": c["prompt_tokens"] + c["completion_tokens"],
                             "latency_s": c["latency_s"]})

    for _ in range(ORCH_MAX_ITERS):
        c0 = time.perf_counter()
        resp, s = _groq_call(messages, api_key, model, ORCH_TOOLS)
        slept += s
        u = resp.get("usage", {})
        log_calls(trigger, "orchestrator", [{
            "prompt_tokens": u.get("prompt_tokens", 0),
            "completion_tokens": u.get("completion_tokens", 0),
            "latency_s": round(time.perf_counter() - c0 - s, 3)}])
        msg = resp["choices"][0]["message"]
        messages.append(msg)
        tcs = msg.get("tool_calls") or []
        if not tcs:
            events.append({"event": "orchestrator_text", "content": (msg.get("content") or "")[:600]})
            break

        sources, done = [], False
        for tc in tcs:
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = None
            if args is None:
                result = json.dumps({"error": "arguments were not valid JSON"})
            elif name == "submit_verdict":
                verdict, done = args, True
                result = json.dumps({"status": "recorded"})
            elif name == "get_claim":
                result = dispatch_tool(name, args)
                sources.append("get_claim")
            elif name in WORKER_OF:
                worker = WORKER_OF[name]
                inp = args if worker == "exclusions" else {"claim_id": args.get("claim_id")}
                status, body = _post_worker(base_url, worker, claim_id, inp)
                if status == 200:
                    slept += body.get("slept_s", 0.0)
                    log_calls(f"orchestrator -> {worker} worker", f"{worker}_worker", body["calls"])
                    result = json.dumps(body["output"] if body["output"] is not None
                                        else {"error": body.get("error")})
                else:
                    result = json.dumps({"error": f"{worker} worker returned HTTP {status}",
                                         "body": body})
                events.append({"event": "worker_call", "worker": worker, "input": inp,
                               "http_status": status, "result": result[:600],
                               "worker_tools": body.get("tools", []) if status == 200 else []})
                sources.append(f"{worker} worker")
            else:
                result = json.dumps({"error": f"unknown tool {name!r}"})
            if name != "submit_verdict":
                events.append({"event": "tool_result", "tool": name, "args": args,
                               "result": result[:600]})
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
        if done:
            events.append({"event": "submit_verdict", "args": verdict})
            break
        trigger = " + ".join(dict.fromkeys(sources)) + " -> orchestrator" if sources \
            else "tool error -> orchestrator"

    latency = time.perf_counter() - t0 - slept
    pt = sum(h["prompt_tokens"] for h in handoffs)
    ct = sum(h["completion_tokens"] for h in handoffs)
    v = verdict or {}
    try:
        payable = float(v.get("payable_amount", 0.0) or 0.0)
    except (TypeError, ValueError):
        payable = 0.0
    return {
        "claim_id": claim_id, "arm": "orchestrator",
        "verdict": v.get("verdict", "no_verdict") if verdict else "no_verdict",
        "payable_amount": payable, "reason": v.get("reason", ""),
        "exclusion_applied": v.get("exclusion_applied"),
        "prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct,
        "cost_usd": round(_cost(pt, ct), 6), "latency_s": round(latency, 3),
        "rate_limit_sleep_s": round(slept, 2), "llm_calls": len(handoffs),
        "handoffs": handoffs, "events": events,
    }


if __name__ == "__main__":
    from week10.workers import start_worker_server
    url = start_worker_server()
    for cid in sys.argv[1:] or ["CLM-001"]:
        r = run_orchestrator(cid, url)
        print(f"\n{cid}: verdict={r['verdict']} payable={r['payable_amount']} "
              f"excl={r['exclusion_applied']} tokens={r['total_tokens']} "
              f"cost=${r['cost_usd']:.6f} latency={r['latency_s']}s calls={r['llm_calls']}")
        for h in r["handoffs"]:
            print(f"  {h['handoff']:<45} {h['agent']:<18} {h['total_tokens']:>6} tok")
        for e in r["events"]:
            if e["event"] == "worker_call":
                print(f"  [worker {e['worker']} HTTP {e['http_status']}] {e['result'][:160]}")
