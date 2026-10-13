"""
Week 10 — the two worker agents, served over local HTTP.

  notes worker       POST /workers/notes       input {"claim_id"}
                     tools: get_adjuster_notes only
                     returns the confirmed damage cause + a 1–2 sentence summary
  exclusions worker  POST /workers/exclusions  input {"cause", "declared_amount"}
                     tools: search_policy_exclusions only
                     returns the exclusions, payout modifier, sub-limit and a
                     coverage statement that keeps every qualifier

Context strategy: each worker gets only what it needs. The notes worker gets a
claim id, not the claim record. The exclusions worker gets a cause and an
amount, not the notes. Neither worker sees the other's output or the
orchestrator's history.

Workers run behind a real HTTP server so a failure can be injected as a real
HTTP 500 (see inject_failure). Same model, temperature and pricing as the
single agent.
"""

import json
import pathlib
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_groq_api_key, get_groq_model, load_env_file
from week7.agent import _cost
from week7.tools import TOOL_SCHEMAS, VALID_CAUSES, dispatch_tool
from week8.agent import _groq_call

load_env_file()

WORKER_MAX_ITERS = 4
_SCHEMA = {t["function"]["name"]: t for t in TOOL_SCHEMAS}


# ── Worker definitions ────────────────────────────────────────────────────────

NOTES_PROMPT = """\
You are the adjuster-notes worker. You are given one claim id.
1. Call get_adjuster_notes with status="reviewed".
2. If the reviewed notes describe a finding as provisional (e.g. "under
   consideration", "pending investigation", "senior review recommended"),
   also call get_adjuster_notes with status="escalated".
3. Call report_notes exactly once. actual_cause is the damage cause the notes
   CONFIRM, which may differ from what the claimant declared. Set confirmed=false
   if the notes leave the cause unconfirmed.
Report only what the notes say. Do not decide coverage or payout.
"""

NOTES_REPORT = {"type": "function", "function": {
    "name": "report_notes",
    "description": "Return your findings to the orchestrator. Call exactly once, last.",
    "parameters": {"type": "object", "properties": {
        "actual_cause": {"type": "string", "enum": VALID_CAUSES,
                         "description": "Damage cause confirmed by the notes."},
        "confirmed":    {"type": "boolean",
                         "description": "True if the notes confirm the cause."},
        "summary":      {"type": "string",
                         "description": "1-2 sentences: what the notes found, with any red flags."},
    }, "required": ["actual_cause", "confirmed", "summary"]},
}}

EXCLUSIONS_PROMPT = """\
You are the coverage/exclusions worker. You are given a damage cause and a
claimed amount.
1. Call search_policy_exclusions with exactly that cause.
2. Call report_coverage exactly once, copying exclusions, payout_modifier and
   sub_limit exactly as returned.
coverage_statement must keep every qualifier. Write "covered subject to <limit
or exclusion>" when a sub-limit or exclusion applies; write "not covered"
when payout_modifier is 0. Never shorten a qualified answer to "covered".
Do not compute the final payout.
"""

EXCLUSIONS_REPORT = {"type": "function", "function": {
    "name": "report_coverage",
    "description": "Return the coverage finding to the orchestrator. Call exactly once, last.",
    "parameters": {"type": "object", "properties": {
        "cause":              {"type": "string", "enum": VALID_CAUSES},
        "exclusions":         {"type": "array", "items": {"type": "string"}},
        "payout_modifier":    {"type": "number"},
        "sub_limit":          {"type": "number"},
        "coverage_statement": {"type": "string"},
    }, "required": ["cause", "exclusions", "payout_modifier", "sub_limit",
                    "coverage_statement"]},
}}

WORKERS = {
    "notes": {
        "prompt":      NOTES_PROMPT,
        "tools":       [_SCHEMA["get_adjuster_notes"], NOTES_REPORT],
        "report_tool": "report_notes",
        "user":        lambda inp: f"Claim id: {inp['claim_id']}",
    },
    "exclusions": {
        "prompt":      EXCLUSIONS_PROMPT,
        "tools":       [_SCHEMA["search_policy_exclusions"], EXCLUSIONS_REPORT],
        "report_tool": "report_coverage",
        "user":        lambda inp: (f"Cause: {inp['cause']}\n"
                                    f"Claimed amount: GBP {inp['declared_amount']}"),
    },
}


def run_worker(worker: str, inp: dict) -> dict:
    """
    Run one worker agent to completion. Returns:
        {"output": dict|None, "calls": [per-LLM-call usage], "tools": [tool calls],
         "latency_s": float, "slept_s": float, "error": str|None}
    """
    spec = WORKERS[worker]
    api_key, model = get_groq_api_key(), get_groq_model()
    messages = [{"role": "system", "content": spec["prompt"]},
                {"role": "user",   "content": spec["user"](inp)}]
    calls, tool_log, slept, output = [], [], 0.0, None
    t0 = time.perf_counter()

    for _ in range(WORKER_MAX_ITERS):
        c0 = time.perf_counter()
        resp, s = _groq_call(messages, api_key, model, spec["tools"])
        slept += s
        u = resp.get("usage", {})
        calls.append({"prompt_tokens": u.get("prompt_tokens", 0),
                      "completion_tokens": u.get("completion_tokens", 0),
                      "latency_s": round(time.perf_counter() - c0 - s, 3)})
        msg = resp["choices"][0]["message"]
        messages.append(msg)
        tcs = msg.get("tool_calls") or []
        if not tcs:
            break
        done = False
        for tc in tcs:
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = None
            if name == spec["report_tool"] and isinstance(args, dict):
                output, done, result = args, True, json.dumps({"status": "received"})
            elif args is None:
                result = json.dumps({"error": "arguments were not valid JSON"})
            elif name in ("get_adjuster_notes", "search_policy_exclusions"):
                result = dispatch_tool(name, args)
            else:
                result = json.dumps({"error": f"tool {name!r} is not available to this worker"})
            tool_log.append({"tool": name, "args": args, "result": result[:400]})
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
        if done:
            break

    return {"output": output, "calls": calls, "tools": tool_log,
            "latency_s": round(time.perf_counter() - t0 - slept, 3),
            "slept_s": round(slept, 2),
            "error": None if output else "worker finished without reporting"}


# ── HTTP service with failure injection ───────────────────────────────────────

_FAIL = set()          # {(worker, claim_id)} -> respond HTTP 500


def inject_failure(worker: str, claim_id: str):
    _FAIL.add((worker, claim_id))


def clear_failures():
    _FAIL.clear()


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):          # keep the race output clean
        pass

    def _reply(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        worker = self.path.rsplit("/", 1)[-1]
        if not self.path.startswith("/workers/") or worker not in WORKERS:
            return self._reply(404, {"error": f"no worker at {self.path}"})
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if (worker, body.get("claim_id")) in _FAIL:
            return self._reply(500, {"error": "Internal Server Error"})
        try:
            self._reply(200, run_worker(worker, body.get("input") or {}))
        except Exception as e:  # a crashed worker is a 500 too
            self._reply(500, {"error": f"{e.__class__.__name__}: {e}"})


def start_worker_server() -> str:
    """Start the worker service on a free local port; return its base URL."""
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}"
