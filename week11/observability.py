"""
Week 11 — tracing, redaction-on-write and the JSONL trace log.

One JSON line per request (a trace). Each trace holds spans, and every span
carries its stage (retrieval | tools | generation), latency, tokens and cost.

Indexed for search, both sides:
  input   query_redacted, input_type, claim_number, user_id
  output  answer_redacted, decision, clauses_cited
  config  prompt_version, retrieval_version, model
  context retrieved context ids (on the retrieval span AND copied to the top level)

PII is redacted BEFORE the line is written (claimant names, titles + names,
emails, phone numbers, postcodes, policy numbers). Adjuster note text and
tool results are never logged; only their size and the ids involved.
"""

import datetime as dt
import json
import pathlib
import re
import time
import uuid

ROOT = pathlib.Path(__file__).resolve().parent.parent

PRICE_PER_1M = 0.90        # USD per 1M tokens in and out, same constant as week7–10


def cost_usd(prompt_tokens: int, completion_tokens: int) -> float:
    return (prompt_tokens + completion_tokens) * PRICE_PER_1M / 1_000_000


# ── Redaction on write ────────────────────────────────────────────────────────

def _known_names():
    p = ROOT / "week9" / "data" / "claims_system.json"
    names = set()
    for c in json.loads(p.read_text()).values():
        full = c["claimant"]
        names.add(full)
        names.update(part for part in full.split() if len(part) > 2)
    return sorted(names, key=len, reverse=True)


_NAMES = _known_names()
_REDACTIONS = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[EMAIL]"),
    (re.compile(r"(?:\+44\s?|\b0)\d{2,4}[\s-]?\d{3,4}[\s-]?\d{3,4}\b"), "[PHONE]"),
    (re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}\b"), "[POSTCODE]"),
    (re.compile(r"\bPOL-\d{4}-\d{3,}\b"), "[POLICY_NO]"),
    (re.compile(r"\b(?:Mr|Mrs|Ms|Miss|Dr)\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?"), "[NAME]"),
]


def redact(text):
    if not text:
        return text
    out = str(text)
    for pat, repl in _REDACTIONS:
        out = pat.sub(repl, out)
    for name in _NAMES:
        out = re.sub(rf"\b{re.escape(name)}\b", "[NAME]", out)
    return out


# ── Trace / span ──────────────────────────────────────────────────────────────

class Span:
    def __init__(self, trace, name, stage):
        self.trace, self.name, self.stage = trace, name, stage
        self.attrs = {}
        self.prompt_tokens = self.completion_tokens = 0
        self.exclude_s = 0.0           # e.g. rate-limit back-off, not real latency

    def __enter__(self):
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *exc):
        elapsed = time.perf_counter() - self._t0 - self.exclude_s
        self.trace.spans.append({
            "span": self.name,
            "stage": self.stage,
            "latency_ms": round(elapsed * 1000, 1),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "tokens": self.prompt_tokens + self.completion_tokens,
            "cost_usd": round(cost_usd(self.prompt_tokens, self.completion_tokens), 7),
            "attrs": self.attrs,
        })
        return False


class Trace:
    def __init__(self, *, user_id, query, input_type, prompt_version,
                 retrieval_version, model, env="prod", at=None):
        self.trace_id = "tr_" + uuid.uuid4().hex[:12]
        self.ts = at or dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        self.user_id, self.query, self.input_type = user_id, query, input_type
        self.prompt_version, self.retrieval_version = prompt_version, retrieval_version
        self.model, self.env = model, env
        self.spans, self.result = [], {}

    def span(self, name, stage):
        return Span(self, name, stage)

    def record(self) -> dict:
        ret = next((s for s in self.spans if s["stage"] == "retrieval"), None)
        return {
            "trace_id":          self.trace_id,
            "ts":                self.ts.isoformat().replace("+00:00", "Z"),
            "env":               self.env,
            "user_id":           self.user_id,
            "input_type":        self.input_type,
            "prompt_version":    self.prompt_version,
            "retrieval_version": self.retrieval_version,
            "model":             self.model,
            "claim_number":      self.result.get("claim_number"),
            "query_redacted":    redact(self.query),
            "answer_redacted":   redact(self.result.get("answer")),
            "decision":          self.result.get("decision"),
            "payable_gbp":       self.result.get("payable_gbp"),
            "clauses_cited":     self.result.get("clauses_cited"),
            "context_ids":       ret["attrs"].get("context_ids") if ret else None,
            "status":            self.result.get("status", "ok"),
            "latency_ms":        round(sum(s["latency_ms"] for s in self.spans), 1),
            "tokens":            sum(s["tokens"] for s in self.spans),
            "cost_usd":          round(sum(s["cost_usd"] for s in self.spans), 7),
            "spans":             self.spans,
        }


def write_trace(record: dict, path):
    """Append the trace, keeping the log file ordered by timestamp."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    if path.exists():
        rows = [l for l in path.read_text().splitlines() if l.strip()]
    rows.append(json.dumps(record))
    rows.sort(key=lambda l: json.loads(l)["ts"])
    path.write_text("\n".join(rows) + "\n")
