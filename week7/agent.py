"""
Week 7 — Insurance claims AGENT.

Runs an LLM agent loop that calls the three tools in whatever order the model
decides.  Four hard budgets are checked at the top of every iteration; hitting
any one terminates the loop cleanly and records which budget fired.

The agent submits its final answer via a submit_verdict tool call rather than
plain-text JSON — this avoids the Groq model occasionally misinterpreting the
output format instruction and generating a fake "JSON" tool call.

Usage:
    python3 week7/agent.py CLM-001          # single claim
    python3 week7/agent.py --all            # all 10 claims
    python3 week7/agent.py CLM-001 --budget-demo  # force max_iters=2 for demo
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
from week7.tools import TOOL_SCHEMAS, dispatch_tool

load_env_file()

# ── Budget constants (enforced every iteration) ───────────────────────────────
MAX_ITERS    = 8      # hard cap on LLM calls per claim
MAX_TOKENS   = 4000   # cumulative tokens across all iterations
MAX_COST_USD = 0.05   # cumulative cost (USD) across all iterations
WALL_CLOCK_S = 30.0   # wall-clock seconds from first call to final answer

# Token pricing for openai/gpt-oss-120b on Groq (USD per 1M tokens)
PRICE_PER_1M_INPUT  = 0.90
PRICE_PER_1M_OUTPUT = 0.90

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# submit_verdict is the agent's way of recording the final decision.
# It is NOT one of the three claims tools — it is the output mechanism.
SUBMIT_VERDICT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "submit_verdict",
        "description": (
            "Submit the final triage verdict for this claim. "
            "Call this once you have gathered all information and computed the payout. "
            "Do not call any other tool after this."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "verdict": {
                    "type": "string",
                    "enum": ["approved", "partial", "denied"],
                    "description": "The triage outcome.",
                },
                "payable_amount": {
                    "type": "number",
                    "description": "The amount payable to the claimant in GBP (0 if denied).",
                },
                "reason": {
                    "type": "string",
                    "description": "One sentence explaining the decision.",
                },
                "exclusion_applied": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                    "description": "The exclusion name that caused a denial or partial, or null if no exclusion applies.",
                },
            },
            "required": ["verdict", "payable_amount", "reason"],
        },
    },
}

ALL_TOOLS = TOOL_SCHEMAS + [SUBMIT_VERDICT_SCHEMA]

SYSTEM_PROMPT = """\
You are an insurance claims adjuster. Triage the claim and submit your verdict.

Steps:
1. Call get_claim to retrieve the claim record.
2. Call get_adjuster_notes with status="reviewed" to read the field-investigator findings.
   The notes often reveal the ACTUAL damage cause, which may differ from the declared type.
3. Call search_policy_exclusions with the ACTUAL cause from the notes (not the declared type).
4. Compute the payable amount:
   - If payout_modifier is 0.0 → verdict = "denied", payable_amount = 0.
   - If declared_amount exceeds sub_limit → cap at sub_limit, verdict = "partial".
   - Otherwise → verdict = "approved", payable_amount = declared_amount - policy_excess.
5. Call submit_verdict with the result.

Rules:
- Apply ONLY the exclusions returned by search_policy_exclusions.
- If a 0.0 payout_modifier exclusion applies, deny regardless of the claimed amount.
- Your last action must be a call to submit_verdict.
"""


def _cost(prompt_tokens: int, completion_tokens: int) -> float:
    return (
        prompt_tokens   * PRICE_PER_1M_INPUT  / 1_000_000
        + completion_tokens * PRICE_PER_1M_OUTPUT / 1_000_000
    )


def _groq_call(messages: list, api_key: str, model: str, tools=None) -> dict:
    """Single Groq API call with retry on 429."""
    payload = {
        "model":       model,
        "messages":    messages,
        "temperature": 0.0,
    }
    if tools:
        payload["tools"]       = tools
        payload["tool_choice"] = "auto"

    for attempt in range(5):
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
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="ignore")
            if e.code == 429 and attempt < 4:
                delay = (2 ** attempt) + 1.5
                time.sleep(delay)
                continue
            raise RuntimeError(f"Groq HTTP {e.code}: {body}")
    raise RuntimeError("Groq: max retries exceeded")


def run_agent(
    claim_id: str,
    api_key:  str,
    model:    str,
    budgets=None,
    log=None,
) -> dict:
    """
    Run the claims agent on a single claim.

    budgets overrides the module-level constants (useful for the budget demo).
    log, if provided, is a list that will be populated with log lines.

    Returns a dict with: claim_id, verdict, payable_amount, reason,
    exclusion_applied, iters, prompt_tokens, completion_tokens, total_tokens,
    cost_usd, latency_s, budget_hit, passed.
    """
    b = {
        "max_iters":    MAX_ITERS,
        "max_tokens":   MAX_TOKENS,
        "max_cost_usd": MAX_COST_USD,
        "wall_clock_s": WALL_CLOCK_S,
    }
    if budgets:
        b.update(budgets)

    def _log(msg: str):
        if log is not None:
            log.append(msg)

    _log(f"[agent] START {claim_id}  budgets={b}")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": f"Triage claim {claim_id}."},
    ]

    t_start          = time.perf_counter()
    total_prompt     = 0
    total_completion = 0
    total_cost       = 0.0
    iters            = 0
    budget_hit       = None
    verdict_raw      = {}

    while True:
        iters += 1
        elapsed = time.perf_counter() - t_start

        # ── Budget checks (all four, every iteration) ─────────────────────────
        if iters > b["max_iters"]:
            budget_hit = "max_iters"
            _log(
                f"[agent] BUDGET HIT: max_iters={b['max_iters']} exceeded "
                f"at start of iteration {iters}. Terminating cleanly."
            )
            break
        tokens_so_far = total_prompt + total_completion
        if tokens_so_far > b["max_tokens"]:
            budget_hit = "max_tokens"
            _log(
                f"[agent] BUDGET HIT: max_tokens={b['max_tokens']} exceeded "
                f"({tokens_so_far} used). Terminating cleanly."
            )
            break
        if total_cost > b["max_cost_usd"]:
            budget_hit = "max_cost"
            _log(
                f"[agent] BUDGET HIT: max_cost_usd=${b['max_cost_usd']:.4f} exceeded "
                f"(${total_cost:.4f} used). Terminating cleanly."
            )
            break
        if elapsed > b["wall_clock_s"]:
            budget_hit = "wall_clock"
            _log(
                f"[agent] BUDGET HIT: wall_clock_s={b['wall_clock_s']}s exceeded "
                f"({elapsed:.1f}s elapsed). Terminating cleanly."
            )
            break

        _log(f"[agent] iter={iters}  elapsed={elapsed:.1f}s  "
             f"tokens={tokens_so_far}  cost=${total_cost:.4f}  "
             f"messages={len(messages)}")

        # ── LLM call ──────────────────────────────────────────────────────────
        resp   = _groq_call(messages, api_key, model, tools=ALL_TOOLS)
        usage  = resp.get("usage", {})
        pt     = usage.get("prompt_tokens", 0)
        ct     = usage.get("completion_tokens", 0)
        total_prompt     += pt
        total_completion += ct
        total_cost       += _cost(pt, ct)

        msg        = resp["choices"][0]["message"]
        tool_calls = msg.get("tool_calls") or []
        messages.append(msg)

        _log(f"[agent] iter={iters}  +{pt}p/{ct}c tokens  "
             f"tool_calls={[tc['function']['name'] for tc in tool_calls]}")

        if not tool_calls:
            # Model returned text with no tool calls — unexpected but handle gracefully
            content = (msg.get("content") or "").strip()
            _log(f"[agent] iter={iters}  no tool_calls — text response: {content[:100]}")
            try:
                verdict_raw = json.loads(content)
            except Exception:
                verdict_raw = {"verdict": "error", "payable_amount": 0.0,
                               "reason": content[:200], "exclusion_applied": None}
            break

        # ── Execute each tool call ─────────────────────────────────────────────
        final_answer = False
        for tc in tool_calls:
            fn_name = tc["function"]["name"]
            fn_args = json.loads(tc["function"]["arguments"])

            if fn_name == "submit_verdict":
                # Final answer via the submit_verdict tool
                verdict_raw = fn_args
                final_answer = True
                _log(f"[agent]   submit_verdict called → {fn_args}")
                # Append a tool result so message history stays valid
                messages.append({
                    "role":         "tool",
                    "tool_call_id": tc["id"],
                    "content":      json.dumps({"status": "recorded"}),
                })
            else:
                result = dispatch_tool(fn_name, fn_args)
                _log(f"[agent]   tool={fn_name}  args={fn_args}  "
                     f"result={result[:120]}")
                messages.append({
                    "role":         "tool",
                    "tool_call_id": tc["id"],
                    "content":      result,
                })

        if final_answer:
            _log(f"[agent] iter={iters}  submit_verdict received — done")
            break

    latency = time.perf_counter() - t_start

    result_dict = {
        "claim_id":          claim_id,
        "verdict":           verdict_raw.get("verdict", "error") if not budget_hit else "budget_hit",
        "payable_amount":    float(verdict_raw.get("payable_amount", 0.0)),
        "reason":            verdict_raw.get("reason", ""),
        "exclusion_applied": verdict_raw.get("exclusion_applied"),
        "iters":             iters - (1 if budget_hit else 0),
        "prompt_tokens":     total_prompt,
        "completion_tokens": total_completion,
        "total_tokens":      total_prompt + total_completion,
        "cost_usd":          round(total_cost, 6),
        "latency_s":         round(latency, 3),
        "budget_hit":        budget_hit,
        "passed":            budget_hit is None,
    }

    _log(f"[agent] DONE  verdict={result_dict['verdict']}  "
         f"latency={latency:.2f}s  tokens={result_dict['total_tokens']}  "
         f"cost=${result_dict['cost_usd']:.6f}  budget_hit={budget_hit}")

    return result_dict


# ── CLI ───────────────────────────────────────────────────────────────────────

def _main():
    api_key = get_groq_api_key()
    model   = get_groq_model()

    args        = sys.argv[1:]
    budget_demo = "--budget-demo" in args
    args        = [a for a in args if a != "--budget-demo"]

    if "--all" in args:
        claims_file = pathlib.Path(__file__).parent / "claims.jsonl"
        claim_ids   = [
            json.loads(l)["claim_id"]
            for l in claims_file.read_text().splitlines()
            if l.strip()
        ]
    else:
        claim_ids = args if args else ["CLM-001"]

    budgets = {"max_iters": 2} if budget_demo else None

    for cid in claim_ids:
        log_lines = []
        r = run_agent(cid, api_key, model, budgets=budgets, log=log_lines)
        print(f"\n{'='*60}")
        print(f"Claim:     {r['claim_id']}")
        print(f"Verdict:   {r['verdict']}  Payable: £{r['payable_amount']:.2f}")
        print(f"Reason:    {r['reason']}")
        print(f"Exclusion: {r['exclusion_applied']}")
        print(f"Iters:     {r['iters']}  Tokens: {r['total_tokens']}  "
              f"Cost: ${r['cost_usd']:.6f}  Latency: {r['latency_s']:.2f}s")
        if r["budget_hit"]:
            print(f"*** BUDGET HIT: {r['budget_hit']} ***")
        print("\n--- Run log ---")
        for line in log_lines:
            print(line)


if __name__ == "__main__":
    _main()
