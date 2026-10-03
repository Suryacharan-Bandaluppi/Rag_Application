"""
Week 7 — Insurance claims FIXED WORKFLOW.

Implements the identical triage task as the agent but as four hard-coded steps
with no loop.  Same tools, same model, same output contract — no adaptive
routing based on what earlier steps return.

Step 1: get_claim(claim_id)
Step 2: get_adjuster_notes(claim_id, status="reviewed")   ← fixed status
Step 3: search_policy_exclusions(declared_type)            ← always uses DECLARED type
Step 4: Single LLM call to compute the verdict

The deliberate limitation: step 3 always looks up the DECLARED claim type, not
the actual cause found in the adjuster notes.  On claims where the notes reveal
a different cause (e.g. flood declared as water_damage, or arson declared as
fire), the workflow gets the wrong exclusion set and the LLM in step 4 must
reason from incomplete information.

Usage:
    python3 week7/workflow.py CLM-001
    python3 week7/workflow.py --all
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
from week7.tools import get_claim, get_adjuster_notes, search_policy_exclusions

load_env_file()

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

PRICE_PER_1M_INPUT  = 0.90
PRICE_PER_1M_OUTPUT = 0.90

SYSTEM_PROMPT = """\
You are an insurance claims adjuster computing a payout verdict.

You have been given:
  CLAIM:      the raw claim record
  NOTES:      adjuster field-investigation notes (status=reviewed)
  EXCLUSIONS: the policy exclusion rules for the declared damage type

Rules:
1. Apply ONLY the exclusions listed in EXCLUSIONS_FOUND below. Do not apply any
   exclusion that is not explicitly present in that block, even if the notes
   suggest a different damage cause.
2. If any exclusion has a payout_modifier of 0.0, verdict = "denied", payable_amount = 0.
3. If declared_amount exceeds the sub_limit in EXCLUSIONS_FOUND, cap at sub_limit
   and set verdict = "partial".
4. Otherwise verdict = "approved", payable_amount = declared_amount - policy_excess.

Output ONLY this JSON (no markdown, no extra text):
{"verdict":"approved|partial|denied","payable_amount":<float>,"reason":"<one sentence>","exclusion_applied":"<exclusion name or null>"}
"""


def _cost(prompt_tokens: int, completion_tokens: int) -> float:
    return (
        prompt_tokens   * PRICE_PER_1M_INPUT  / 1_000_000
        + completion_tokens * PRICE_PER_1M_OUTPUT / 1_000_000
    )


def _groq_call(messages: list, api_key: str, model: str) -> dict:
    """Single Groq API call with retry on 429."""
    payload = {
        "model":       model,
        "messages":    messages,
        "temperature": 0.0,
    }
    for attempt in range(5):
        try:
            req = urllib.request.Request(
                GROQ_URL,
                data=json.dumps(payload).encode(),
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type":  "application/json",
                    "User-Agent":    "ClaimsWorkflow/1.0",
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


def run_workflow(claim_id: str, api_key: str, model: str) -> dict:
    """
    Run the fixed 4-step workflow on a single claim.

    Returns the same dict shape as run_agent() so race.py can compare them
    directly.  The 'iters' field is always 1 (one LLM call).
    """
    t_start = time.perf_counter()

    # ── Step 1: get_claim ──────────────────────────────────────────────────────
    claim = get_claim(claim_id)
    if "error" in claim:
        return {
            "claim_id": claim_id, "verdict": "error",
            "payable_amount": 0.0, "reason": claim["error"],
            "exclusion_applied": None, "iters": 0,
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
            "cost_usd": 0.0, "latency_s": 0.0, "budget_hit": None, "passed": False,
        }

    # ── Step 2: get_adjuster_notes (fixed: status="reviewed") ─────────────────
    notes_result = get_adjuster_notes(claim_id, "reviewed")
    notes_text   = notes_result.get("notes", "")

    # ── Step 3: search_policy_exclusions (always uses DECLARED type) ───────────
    # Deliberate design: the workflow never re-routes based on notes content.
    # It always queries the declared claim type, so it misses exclusions that
    # only apply after the adjuster identifies a different actual cause.
    exclusions = search_policy_exclusions(claim["declared_type"])

    # ── Step 4: single LLM call ───────────────────────────────────────────────
    user_content = (
        f"CLAIM:\n{json.dumps(claim, indent=2)}\n\n"
        f"ADJUSTER_NOTES (reviewed):\n{notes_text or '(none)'}\n\n"
        f"EXCLUSIONS_FOUND (for declared type '{claim['declared_type']}'):\n"
        f"{json.dumps(exclusions, indent=2)}"
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": user_content},
    ]

    resp  = _groq_call(messages, api_key, model)
    usage = resp.get("usage", {})
    pt    = usage.get("prompt_tokens", 0)
    ct    = usage.get("completion_tokens", 0)
    cost  = _cost(pt, ct)

    raw_text = resp["choices"][0]["message"]["content"].strip()
    if raw_text.startswith("```"):
        raw_text = raw_text.split("```")[1]
        if raw_text.startswith("json"):
            raw_text = raw_text[4:]
    try:
        verdict_raw = json.loads(raw_text.strip())
    except Exception:
        verdict_raw = {
            "verdict": "error", "payable_amount": 0.0,
            "reason": raw_text[:200], "exclusion_applied": None,
        }

    latency = time.perf_counter() - t_start

    return {
        "claim_id":          claim_id,
        "verdict":           verdict_raw.get("verdict", "error"),
        "payable_amount":    float(verdict_raw.get("payable_amount", 0.0)),
        "reason":            verdict_raw.get("reason", ""),
        "exclusion_applied": verdict_raw.get("exclusion_applied"),
        "iters":             1,
        "prompt_tokens":     pt,
        "completion_tokens": ct,
        "total_tokens":      pt + ct,
        "cost_usd":          round(cost, 6),
        "latency_s":         round(latency, 3),
        "budget_hit":        None,
        "passed":            True,
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def _main():
    api_key = get_groq_api_key()
    model   = get_groq_model()

    args = sys.argv[1:]
    if "--all" in args:
        import pathlib as _pl
        claims_file = _pl.Path(__file__).parent / "claims.jsonl"
        claim_ids = [
            json.loads(l)["claim_id"]
            for l in claims_file.read_text().splitlines()
            if l.strip()
        ]
    else:
        claim_ids = args if args else ["CLM-001"]

    for cid in claim_ids:
        r = run_workflow(cid, api_key, model)
        print(f"\n{'='*60}")
        print(f"Claim: {r['claim_id']}")
        print(f"Verdict: {r['verdict']}  Payable: £{r['payable_amount']:.2f}")
        print(f"Reason:  {r['reason']}")
        print(f"Exclusion: {r['exclusion_applied']}")
        print(f"Iters: {r['iters']}  Tokens: {r['total_tokens']}  "
              f"Cost: ${r['cost_usd']:.6f}  Latency: {r['latency_s']:.2f}s")


if __name__ == "__main__":
    _main()
