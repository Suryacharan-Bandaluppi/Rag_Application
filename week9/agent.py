"""
Week 9 — Claims agent as an MCP HOST.

This module never names a server or a tool. Everything the model can call is
discovered at start-up:

    mcp_config.json  ->  spawn each server  ->  initialize  ->  tools/list
                                                            ->  resources/list + resources/read

Discovered tools are exposed to the model as "<server>__<tool>"; resources are
attached to the system prompt as context (app-attached, never model-invoked).
Adding a server is a config change only — this file does not change.

Where the model call happens: ONLY in _groq_call() below, inside the host.
MCP servers never call a model; the MCP wire carries no LLM traffic.

Usage:
    python3 week9/agent.py --discover                       # live tools/list per server
    python3 week9/agent.py "What is the status of claim CLM-2024-88102?"
    python3 week9/agent.py --config path/to/config.json "..." --trace-out t.json
"""

import argparse
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

WEEK9 = pathlib.Path(__file__).resolve().parent
ROOT  = WEEK9.parent
sys.path.insert(0, str(ROOT))

from app.config import get_groq_api_key, get_groq_model, load_env_file
from week9.mcp_client import StdioMCPClient, MCPError

load_env_file()

DEFAULT_CONFIG = WEEK9 / "mcp_config.json"
LOG_DIR        = WEEK9 / "logs"
GROQ_URL       = "https://api.groq.com/openai/v1/chat/completions"
MAX_ITERS      = 8
SEP            = "__"          # qualified tool name = <server>__<tool>

SYSTEM_PROMPT = """\
You are an insurance claims triage assistant. Answer using the tools you have
been given and the attached context.
- State only facts that came from a tool result or the attached context.
- If a tool returns an error, read it: it tells you whether to correct the
  argument and retry, or to stop and tell the user what is needed.
- If you cannot load the information you need, say so plainly. Never answer
  about coverage for a claim you could not load.
"""

_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _expand(value: str) -> str:
    """Expand ${VAR} and ${VAR:-default} from the environment."""
    return _VAR.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), value)


def load_config(path) -> dict:
    cfg = json.loads(pathlib.Path(path).read_text())
    for spec in cfg.get("mcpServers", {}).values():
        spec["args"] = [_expand(a) for a in spec.get("args", [])]
        spec["env"]  = {k: _expand(v) for k, v in spec.get("env", {}).items()}
    return cfg


# ── MCP host: connect, discover, route ────────────────────────────────────────

class MCPHost:
    def __init__(self, config_path=DEFAULT_CONFIG, wire=None):
        self.config_path = pathlib.Path(config_path).resolve()
        self.config      = load_config(self.config_path)
        self.wire        = wire
        self.clients     = {}      # server name -> StdioMCPClient
        self.tools       = {}      # qualified name -> {"server", "tool", "description", "inputSchema"}
        self.contexts    = []      # attached resources: {"server", "uri", "text"}

    def connect(self):
        LOG_DIR.mkdir(exist_ok=True)
        for name, spec in self.config.get("mcpServers", {}).items():
            if spec.get("disabled"):
                continue
            client = StdioMCPClient(
                name, spec["command"], spec.get("args", []), env=spec.get("env"),
                cwd=str(self.config_path.parent),
                stderr_path=str(LOG_DIR / f"{name}.stderr.log"), wire=self.wire,
            ).start()
            init = client.initialize()
            self.clients[name] = client

            for t in client.list_tools():
                self.tools[f"{name}{SEP}{t['name']}"] = {
                    "server": name, "tool": t["name"],
                    "description": t.get("description", ""),
                    "inputSchema": t.get("inputSchema") or {"type": "object", "properties": {}},
                }
            if "resources" in init.get("capabilities", {}):
                for r in client.list_resources():
                    for c in client.read_resource(r["uri"]).get("contents", []):
                        if "text" in c:
                            self.contexts.append({"server": name, "uri": r["uri"], "text": c["text"]})
        return self

    def close(self):
        for c in self.clients.values():
            c.close()

    def discovered(self) -> dict:
        out = {}
        for q, t in self.tools.items():
            out.setdefault(t["server"], []).append(t["tool"])
        return out

    def model_tools(self) -> list:
        return [{"type": "function", "function": {
                    "name": q, "description": t["description"], "parameters": t["inputSchema"]}}
                for q, t in self.tools.items()]

    def system_prompt(self) -> str:
        if not self.contexts:
            return SYSTEM_PROMPT
        blocks = [f"### {c['uri']} (from {c['server']})\n{c['text']}" for c in self.contexts]
        return SYSTEM_PROMPT + "\n## Attached context (MCP resources)\n\n" + "\n\n".join(blocks)

    def call(self, qualified: str, args: dict):
        """Route a model tool call to its server. Returns (text, is_error)."""
        t = self.tools.get(qualified)
        if t is None:
            return f"unknown tool {qualified!r}; available: {sorted(self.tools)}", True
        try:
            res = self.clients[t["server"]].call_tool(t["tool"], args)
        except MCPError as e:
            return str(e), True
        text = "\n".join(c.get("text", "") for c in res.get("content", []) if c.get("type") == "text")
        return text, bool(res.get("isError"))


# ── Model call (the ONLY place a model is invoked) ────────────────────────────

def _groq_call(messages, tools, api_key, model):
    payload = {"model": model, "messages": messages, "temperature": 0.0,
               "tools": tools, "tool_choice": "auto"}
    for attempt in range(6):
        try:
            req = urllib.request.Request(
                GROQ_URL, data=json.dumps(payload).encode(), method="POST",
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json", "User-Agent": "ClaimsAgent/1.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="ignore")
            if e.code in (429, 500, 502, 503) and attempt < 5:
                time.sleep(2 ** attempt + 1.5)
                continue
            raise RuntimeError(f"Groq HTTP {e.code}: {body}")
    raise RuntimeError("Groq: max retries exceeded")


def run(query: str, config_path=DEFAULT_CONFIG, seed_tool_calls=None, max_iters=MAX_ITERS) -> dict:
    """
    Answer one query. seed_tool_calls, if given, is a list of
    (qualified_tool_name, args) injected as the assistant's first turn — the
    calls are executed against the live servers and the model takes it from
    there. Used to replay an identical failing call in a before/after test.
    """
    api_key, model = get_groq_api_key(), get_groq_model()
    host = MCPHost(config_path).connect()
    trace, tokens = [], 0
    try:
        tools = host.model_tools()
        messages = [{"role": "system", "content": host.system_prompt()},
                    {"role": "user", "content": query}]

        def execute(calls):
            for tc in calls:
                name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args, text, is_err = None, "tool arguments were not valid JSON", True
                else:
                    text, is_err = host.call(name, args)
                t = host.tools.get(name, {})
                trace.append({"type": "tool_call", "server": t.get("server"),
                              "tool": t.get("tool"), "qualified": name,
                              "args": args, "is_error": is_err, "result": text})
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": text})

        if seed_tool_calls:
            calls = [{"id": f"seed_{i}", "type": "function",
                      "function": {"name": n, "arguments": json.dumps(a)}}
                     for i, (n, a) in enumerate(seed_tool_calls)]
            messages.append({"role": "assistant", "content": None, "tool_calls": calls})
            trace.append({"type": "seeded_turn", "calls": [[n, a] for n, a in seed_tool_calls]})
            execute(calls)

        answer = None
        for _ in range(max_iters):
            resp = _groq_call(messages, tools, api_key, model)      # <- MODEL CALL
            tokens += resp.get("usage", {}).get("total_tokens", 0)
            msg = resp["choices"][0]["message"]
            messages.append(msg)
            calls = msg.get("tool_calls") or []
            if not calls:
                answer = (msg.get("content") or "").strip()
                break
            execute(calls)
        return {"query": query, "model": model,
                "discovered": host.discovered(),
                "attached_resources": [c["uri"] for c in host.contexts],
                "trace": trace, "answer": answer, "total_tokens": tokens,
                "llm_calls": sum(1 for m in messages if m.get("role") == "assistant")
                             - (1 if seed_tool_calls else 0)}
    finally:
        host.close()


# ── CLI ───────────────────────────────────────────────────────────────────────

def _print_run(r):
    print(f"discovered: {json.dumps(r['discovered'])}")
    print(f"attached resources: {r['attached_resources']}")
    for s in r["trace"]:
        if s["type"] == "seeded_turn":
            print(f"  [seeded first turn] {s['calls']}")
        else:
            flag = " ERROR" if s["is_error"] else ""
            print(f"  tool_call server={s['server']} tool={s['tool']} "
                  f"args={json.dumps(s['args'])}{flag}\n    -> {s['result'][:300]}")
    print(f"answer ({r['llm_calls']} model calls, {r['total_tokens']} tokens):\n{r['answer']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="?")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--discover", action="store_true")
    ap.add_argument("--trace-out")
    a = ap.parse_args()

    if a.discover:
        host = MCPHost(a.config).connect()
        try:
            d = host.discovered()
            n = sum(len(v) for v in d.values())
            print(f"{n} tools discovered via tools/list from {len(d)} server(s):")
            for server, names in d.items():
                print(f"  {server}: {', '.join(names)}")
            print(f"resources attached as context: {[c['uri'] for c in host.contexts]}")
        finally:
            host.close()
        return

    if not a.query:
        ap.error("give a query or --discover")
    r = run(a.query, a.config)
    _print_run(r)
    if a.trace_out:
        pathlib.Path(a.trace_out).write_text(json.dumps(r, indent=2))


if __name__ == "__main__":
    main()
