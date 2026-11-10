"""
Week 11 — Claims coverage assistant (production app), fully traced.

Pipeline for one adjuster question:
  1. retrieval   BM25 over the policy wording -> top-3 clause ids (local, $0)
  2. tools       LLM turns that call get_claim, plus the tool execution itself
  3. generation  the LLM turn that writes the final JSON answer

Every request is written as one trace to week11/logs/traces.jsonl (redacted
on write). See observability.py for the record schema.

Usage:
    python3 week11/app.py ask --user adj-03 "Is claim CLM-2024-88104 covered?"
    python3 week11/app.py ask --user adj-03 --at 2026-10-26T14:05:09Z --log path "..."
"""

import argparse
import datetime as dt
import json
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

WEEK11 = pathlib.Path(__file__).resolve().parent
ROOT   = WEEK11.parent
sys.path.insert(0, str(ROOT))

from app.config import get_groq_api_key, get_groq_model, load_env_file
from week11.observability import Trace, write_trace
from week11.prompts import CURRENT_PROMPT_VERSION, PROMPTS, RETRIEVAL_FOR
from week11.retrieval import CLAUSES, EXCLUSION_CLAUSES, retrieve, retrieve_v2

load_env_file()

PROD_LOG  = WEEK11 / "logs" / "traces.jsonl"
GROQ_URL  = "https://api.groq.com/openai/v1/chat/completions"
MAX_TURNS = 4
CLAIM_RE  = re.compile(r"\bCLM-\d{4}-\d{5}\b")
_CLAIMS   = json.loads((ROOT / "week9" / "data" / "claims_system.json").read_text())

GET_CLAIM_SCHEMA = {"type": "function", "function": {
    "name": "get_claim",
    "description": "Get one claim's record (status, declared type, declared amount, "
                   "policy excess) and its full adjuster note history, by claim number "
                   "(CLM-YYYY-nnnnn).",
    "parameters": {"type": "object", "properties": {
        "claim_number": {"type": "string"}}, "required": ["claim_number"]},
}}


SUBMIT_SCHEMA = {"type": "function", "function": {
    "name": "submit_answer",
    "description": "Submit the final answer to the adjuster. Call exactly once, last.",
    "parameters": {"type": "object", "properties": {
        "decision": {"type": "string",
                     "enum": ["covered", "partially_covered", "not_covered", "cannot_determine"]},
        "payable_gbp": {"anyOf": [{"type": "number"}, {"type": "null"}]},
        "clauses_cited": {"type": "array", "items": {"type": "string"}},
        "answer": {"type": "string", "description": "2-3 sentences for the adjuster."}},
        "required": ["decision", "clauses_cited", "answer"]},
}}


def get_claim(claim_number: str) -> dict:
    c = _CLAIMS.get(str(claim_number).strip())
    if c is None:
        return {"error": f"claim {claim_number} not found: claim numbers look like "
                         f"CLM-YYYY-nnnnn; check for a typo"}
    return {k: v for k, v in c.items() if k != "legacy_id"}


def classify_input(q: str) -> str:
    if CLAIM_RE.search(q):
        return "claim_lookup"
    if re.search(r"\b(excess|deductible)\b", q, re.I):
        return "excess_question"
    return "coverage_question"


def _llm(messages, api_key, model):
    """One Groq call. Returns (response, seconds slept on 429/5xx back-off)."""
    payload = {"model": model, "messages": messages, "temperature": 0.0,
               "tools": [GET_CLAIM_SCHEMA, SUBMIT_SCHEMA], "tool_choice": "auto"}
    slept, rejected = 0.0, 0
    for attempt in range(6):
        try:
            req = urllib.request.Request(
                GROQ_URL, data=json.dumps(payload).encode(), method="POST",
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json", "User-Agent": "CoverageAssistant/1.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                resp = json.loads(r.read())
                resp["_meta"] = {"rejected_calls": rejected}
                return resp, slept
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="ignore")
            if e.code in (429, 500, 502, 503) and attempt < 5:
                d = 2 ** attempt + 1.5
                time.sleep(d)
                slept += d
                continue
            # gpt-oss sometimes names a tool that does not exist (e.g. "json",
            # "commentary"); Groq rejects the call with 400 tool_use_failed and
            # reports no token usage for it.
            try:
                err = json.loads(body).get("error", {})
            except json.JSONDecodeError:
                err = {}
            if e.code == 400 and err.get("code") == "tool_use_failed":
                rejected += 1
                if rejected <= 2:
                    continue                                   # retry as-is
                try:
                    gen = json.loads(err.get("failed_generation") or "{}")
                except json.JSONDecodeError:
                    gen = {}
                args = gen.get("arguments") if isinstance(gen, dict) else None
                if isinstance(args, dict) and "decision" in args:
                    # the payload is the final answer under the wrong tool name:
                    # repair it into submit_answer and record that we did
                    return {"choices": [{"message": {"role": "assistant", "content": None,
                            "tool_calls": [{"id": "repaired_0", "type": "function", "function": {
                                "name": "submit_answer", "arguments": json.dumps(args)}}]}}],
                            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
                            "_meta": {"rejected_calls": rejected,
                                      "repaired_from_tool": gen.get("name")}}, slept
            raise RuntimeError(f"Groq HTTP {e.code}: {body}")
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
            if attempt < 5:          # dropped connection / timeout: back off and retry
                d = 2 ** attempt + 1.5
                time.sleep(d)
                slept += d
                continue
            raise RuntimeError(f"Groq connection failed: {e}")
    raise RuntimeError("Groq: max retries exceeded")


def _parse_submit(args_json: str) -> dict:
    try:
        d = json.loads(args_json or "{}")
        return {"decision": d.get("decision"), "payable_gbp": d.get("payable_gbp"),
                "clauses_cited": [str(x).strip().strip("[]").strip()
                                  for x in (d.get("clauses_cited") or [])],
                "answer": d.get("answer"), "status": "ok"}
    except Exception:
        return {"decision": "unparseable", "payable_gbp": None, "clauses_cited": [],
                "answer": (args_json or "")[:1000], "status": "unparseable_output"}


def answer(question: str, user_id: str, *, at=None, log_path=PROD_LOG,
           prompt_version: str = None, env: str = "prod") -> dict:
    prompt_version = prompt_version or CURRENT_PROMPT_VERSION
    api_key, model = get_groq_api_key(), get_groq_model()
    retrieval_version = RETRIEVAL_FOR[prompt_version]
    tr = Trace(user_id=user_id, query=question, input_type=classify_input(question),
               prompt_version=prompt_version, retrieval_version=retrieval_version,
               model=model, env=env, at=at)
    m = CLAIM_RE.search(question)
    tr.result["claim_number"] = m.group(0) if m else None

    # 1. retrieval
    with tr.span("retrieve.policy_clauses", "retrieval") as sp:
        if retrieval_version == "bm25-k3-v1":
            hits = retrieve(question)
            ids = [c for c, _ in hits]
            sp.attrs = {"top_k": len(hits), "context_ids": ids, "scores": [s for _, s in hits]}
        else:
            claim = None
            if m:
                rec = get_claim(m.group(0))
                claim = None if "error" in rec else rec
            r = retrieve_v2(question, claim)
            ids = r["context_ids"]
            sp.attrs = {"top_k": len(r["retrieved"]), "context_ids": ids,
                        "retrieved_ids": r["retrieved"], "scores": r["scores"],
                        "added_ids": r["added"], "query_sources": r["query_sources"]}

    fmt = lambda c: f"[{c}] {CLAUSES[c]['title']}: {CLAUSES[c]['text']}"
    if "{exclusions}" in PROMPTS[prompt_version]:
        covers = [c for c in ids if c not in EXCLUSION_CLAUSES]
        excls = [c for c in ids if c in EXCLUSION_CLAUSES]
        system = PROMPTS[prompt_version].format(
            context="\n".join(fmt(c) for c in covers) or "(none)",
            exclusions="\n".join(fmt(c) for c in excls) or "(none retrieved)")
    else:
        system = PROMPTS[prompt_version].format(context="\n".join(fmt(c) for c in ids))

    messages = [{"role": "system", "content": system},
                {"role": "user", "content": question}]
    result, nudged = None, False
    for _ in range(MAX_TURNS + 1):
        t0 = time.perf_counter()
        resp, slept = _llm(messages, api_key, model)
        call_s = time.perf_counter() - t0 - slept
        u = resp.get("usage", {})
        meta = {k: v for k, v in resp.get("_meta", {}).items() if v}
        if meta:   # rejected calls consume tokens Groq never reports to us
            meta["untracked_tokens"] = True
        msg = resp["choices"][0]["message"]
        messages.append(msg)
        tcs = msg.get("tool_calls") or []
        submit = next((tc for tc in tcs if tc["function"]["name"] == "submit_answer"), None)

        if submit is not None:   # 3. generation: the turn that writes the final answer
            with tr.span("llm.generate", "generation") as sp:
                sp.prompt_tokens = u.get("prompt_tokens", 0)
                sp.completion_tokens = u.get("completion_tokens", 0)
                sp.exclude_s = -call_s       # span latency = this LLM call
                result = _parse_submit(submit["function"].get("arguments"))
                sp.attrs = {"decision": result["decision"], **meta}
            break

        if tcs:   # 2. tools: the LLM turn that asked for the tool + the tool itself
            with tr.span("llm.tool_call", "tools") as sp:
                sp.prompt_tokens = u.get("prompt_tokens", 0)
                sp.completion_tokens = u.get("completion_tokens", 0)
                sp.attrs = {"tool_calls": [tc["function"]["name"] for tc in tcs], **meta}
                sp.exclude_s = -call_s
            for tc in tcs:
                with tr.span(f"tool.{tc['function']['name']}", "tools") as sp:
                    try:
                        args = json.loads(tc["function"].get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    out = get_claim(args.get("claim_number")) if tc["function"]["name"] == "get_claim" \
                        else {"error": "unknown tool"}
                    sp.attrs = {"claim_number": args.get("claim_number"),
                                "found": "error" not in out,
                                "result_chars": len(json.dumps(out))}   # never the content
                messages.append({"role": "tool", "tool_call_id": tc["id"],
                                 "content": json.dumps(out)})
            continue

        # plain text instead of submit_answer: remind once, then accept it unstructured
        if not nudged:
            nudged = True
            with tr.span("llm.format_retry", "generation") as sp:
                sp.prompt_tokens = u.get("prompt_tokens", 0)
                sp.completion_tokens = u.get("completion_tokens", 0)
                sp.exclude_s = -call_s
                sp.attrs = {"format_nudge": True, **meta}
            messages.append({"role": "user", "content":
                             "Submit that answer by calling submit_answer."})
            continue
        with tr.span("llm.generate", "generation") as sp:
            sp.prompt_tokens = u.get("prompt_tokens", 0)
            sp.completion_tokens = u.get("completion_tokens", 0)
            sp.exclude_s = -call_s
            result = {"decision": "unstructured", "payable_gbp": None, "clauses_cited": [],
                      "answer": (msg.get("content") or "")[:1000], "status": "no_submit"}
            sp.attrs = {"decision": result["decision"], **meta}
        break

    if result is None:
        result = {"decision": "no_answer", "payable_gbp": None, "clauses_cited": [],
                  "answer": None, "status": "max_turns"}
    tr.result.update(result)
    rec = tr.record()
    write_trace(rec, log_path)
    return rec


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("ask")
    a.add_argument("question")
    a.add_argument("--user", required=True)
    a.add_argument("--at", help="ISO timestamp to record (replaying traffic)")
    a.add_argument("--log", default=str(PROD_LOG))
    a.add_argument("--prompt-version")
    a.add_argument("--env", default="prod")
    args = ap.parse_args()
    at = dt.datetime.fromisoformat(args.at.replace("Z", "+00:00")) if args.at else None
    rec = answer(args.question, args.user, at=at, log_path=args.log,
                 prompt_version=args.prompt_version, env=args.env)
    print(json.dumps({k: rec[k] for k in ("trace_id", "ts", "decision", "payable_gbp",
                                         "clauses_cited", "context_ids", "answer_redacted",
                                         "tokens", "cost_usd", "latency_ms")}, indent=1))


if __name__ == "__main__":
    main()
