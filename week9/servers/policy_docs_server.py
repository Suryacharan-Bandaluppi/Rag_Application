"""
Week 9 — Server ONE (ours): policy-document search MCP server.

Transport: MCP stdio — newline-delimited JSON-RPC 2.0 on stdin/stdout,
logs on stderr. Standard library only (this machine's Python is 3.9 and the
official `mcp` SDK needs 3.10+), so the protocol is implemented directly.

Exposes:
  tools      search_policy_documents, get_policy_clause   (model-invoked)
  resources  policy://exclusions-schedule                  (app-attached)

The exclusions schedule is deliberately a RESOURCE, not a tool: it is context
the host attaches up front, so the model never has to call out for a list it
should already have been given.

Every tool's description is its Python docstring — the docstring IS the prompt
the model reads in tools/list.

No model is called anywhere in this file. The server exposes capabilities;
the host (week9/agent.py) runs the model.
"""

import inspect
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

from week7.tools import POLICY_DB

SERVER_INFO       = {"name": "policy-docs", "version": "1.0.0"}
PROTOCOL_VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05"]
DOC_PATH          = ROOT / "week9" / "data" / "policy_document.md"


def _log(msg: str):
    print(f"[policy-docs] {msg}", file=sys.stderr, flush=True)


# ── Corpus ────────────────────────────────────────────────────────────────────

def _load_sections() -> dict:
    sections, cur = {}, None
    for line in DOC_PATH.read_text().splitlines():
        m = re.match(r"^## (\S+) (.+)$", line)
        if m:
            cur = m.group(1)
            sections[cur] = {"clause_id": cur, "title": m.group(2), "text": ""}
        elif cur and line.strip():
            sections[cur]["text"] += line.strip() + " "
    for s in sections.values():
        s["text"] = s["text"].strip()
    return sections


SECTIONS = _load_sections()

# cause -> clause id, for the exclusions schedule resource
CAUSE_CLAUSE = {
    "water_damage": "3.1", "flood": "4.2b", "fire": "5.1", "arson": "8.1",
    "theft": "7.1", "hail_damage": "7.4", "vandalism": "7.5",
    "vehicle_accident": "6.1", "intoxicated_driving": "6.3",
    "tree_damage": "7.6", "lightning": "7.7", "medical": "9.1",
}


def _exclusions_schedule() -> str:
    lines = ["# Policy exclusions schedule",
             "| cause | clause | exclusions | payout_modifier | sub_limit_gbp |",
             "|---|---|---|---|---|"]
    for cause, row in POLICY_DB.items():
        lines.append(f"| {cause} | {CAUSE_CLAUSE[cause]} | {', '.join(row['exclusions'])} "
                     f"| {row['payout_modifier']} | {row['sub_limit']:.0f} |")
    return "\n".join(lines)


RESOURCES = {
    "policy://exclusions-schedule": {
        "name": "exclusions-schedule",
        "title": "Policy exclusions schedule",
        "description": "Every damage cause with its clause id, exclusions, payout "
                       "modifier and sub-limit. Attach as context; do not fetch per call.",
        "mimeType": "text/markdown",
        "read": _exclusions_schedule,
    },
}


# ── Tools ─────────────────────────────────────────────────────────────────────

TOOLS = {}


def tool(input_schema: dict):
    def register(fn):
        TOOLS[fn.__name__] = {
            "fn": fn,
            "description": inspect.cleandoc(fn.__doc__),
            "inputSchema": input_schema,
        }
        return fn
    return register


def _tokens(s: str) -> set:
    return set(re.findall(r"[a-z0-9]+", s.lower()))


@tool({
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "Free-text search over the policy wording."},
        "top_k": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3},
    },
    "required": ["query"],
})
def search_policy_documents(query: str, top_k: int = 3):
    """
    Search the policy wording by keyword and return the best-matching clauses
    (clause id, title and the start of the clause text). Use it when you know the
    topic (e.g. "flood", "arson") but not the clause id.
    """
    q = _tokens(query)
    scored = []
    for s in SECTIONS.values():
        score = len(q & _tokens(s["title"])) * 2 + len(q & _tokens(s["text"]))
        if score:
            scored.append((score, s))
    scored.sort(key=lambda x: (-x[0], x[1]["clause_id"]))
    hits = [{"clause_id": s["clause_id"], "title": s["title"], "snippet": s["text"][:160]}
            for _, s in scored[: max(1, min(int(top_k), 5))]]
    if not hits:
        return json.dumps({"results": [], "note": f"no clause matched {query!r}"}), False
    return json.dumps({"results": hits}), False


@tool({
    "type": "object",
    "properties": {
        "clause_id": {
            "type": "string",
            "description": "Clause id exactly as in the policy, e.g. '6.3' or '8.1'. "
                           "No 'Clause' prefix; keep any letter suffix.",
        },
    },
    "required": ["clause_id"],
})
def get_policy_clause(clause_id: str):
    """
    Return the full, verbatim text of ONE policy clause, given its clause id.

    Use this whenever you must quote or apply a specific clause: one cited in a
    customer letter, in an adjuster note ("Clause 8.1") or in the exclusions
    schedule. Quote only from this tool's output, never from a search snippet;
    snippets are truncated.

    clause_id is the section number plus an optional letter suffix, exactly as
    printed in the policy: '3.1', '6.3', '8.1'. Drop any 'Clause' prefix. The
    letter is part of the id; an id without its letter is a different id.

    Returns JSON {clause_id, title, text}. If the id does not exist, the error
    names the real ids closest to what you asked for: pick the one that fits the
    question and call again. Do not guess or paraphrase the clause wording.
    """
    asked = str(clause_id).strip()
    s = SECTIONS.get(asked)
    if s is not None:
        return json.dumps(s), False

    norm = re.sub(r"(?i)^clause\s*", "", asked).strip()
    if norm in SECTIONS:
        return (f"clause {asked!r} not found: drop the 'Clause' prefix — "
                f"call again with clause_id='{norm}'."), True
    near = [cid for cid in SECTIONS if cid.startswith(norm)] if norm else []
    if near:
        options = ", ".join(f"'{c}' ({SECTIONS[c]['title']})" for c in near)
        return (f"clause {asked!r} not found: clause ids include a letter suffix where "
                f"one exists. Ids starting with '{norm}': {options}. Call again with "
                f"the id that matches the question."), True
    every = ", ".join(f"'{c}' ({s['title']})" for c, s in SECTIONS.items())
    return (f"clause {asked!r} not found: clause ids look like '6.3' or '4.2b'. "
            f"Valid ids: {every}. Call again with one of these, or use "
            f"search_policy_documents if you only know the topic."), True


# ── JSON-RPC / MCP plumbing ───────────────────────────────────────────────────

def _send(msg: dict):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def _result(mid, result):
    _send({"jsonrpc": "2.0", "id": mid, "result": result})


def _error(mid, code, message):
    _send({"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}})


def handle(msg: dict):
    method, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    is_request = "id" in msg

    if method == "initialize":
        asked = params.get("protocolVersion")
        _result(mid, {
            "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False},
                             "resources": {"subscribe": False, "listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": "Policy wording search. The exclusions schedule is a resource.",
        })
    elif method == "notifications/initialized":
        _log("client initialized")
    elif method == "ping":
        _result(mid, {})
    elif method == "tools/list":
        _result(mid, {"tools": [
            {"name": n, "description": t["description"], "inputSchema": t["inputSchema"]}
            for n, t in TOOLS.items()
        ]})
    elif method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in TOOLS:
            return _error(mid, -32602, f"Unknown tool: {name}")
        try:
            text, is_error = TOOLS[name]["fn"](**args)
        except TypeError as e:
            text, is_error = f"invalid arguments for {name}: {e}", True
        _log(f"tools/call {name} {json.dumps(args)} isError={is_error}")
        _result(mid, {"content": [{"type": "text", "text": text}], "isError": is_error})
    elif method == "resources/list":
        _result(mid, {"resources": [
            {"uri": uri, **{k: v for k, v in r.items() if k != "read"}}
            for uri, r in RESOURCES.items()
        ]})
    elif method == "resources/read":
        uri = params.get("uri")
        if uri not in RESOURCES:
            return _error(mid, -32002, f"Resource not found: {uri}")
        r = RESOURCES[uri]
        _result(mid, {"contents": [{"uri": uri, "mimeType": r["mimeType"], "text": r["read"]()}]})
    elif is_request:
        _error(mid, -32601, f"Method not found: {method}")
    # unknown notifications are ignored, per JSON-RPC


def main():
    _log(f"started, {len(TOOLS)} tools, {len(RESOURCES)} resources")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            _error(None, -32700, "Parse error")
            continue
        handle(msg)


if __name__ == "__main__":
    main()
