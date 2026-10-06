"""
Claims-system MCP server — owned by the Claims Platform team.

(Course stand-in: in production this is the platform team's code, shipped to us
as-is. It lives under week9/third_party/ to keep it outside our own code; we
add it to the agent through config only, and audit it in risk_note.md.)

Transport: MCP stdio (newline-delimited JSON-RPC 2.0), standard library only.

Tools:
  get_claim_status(claim_number)          — current status of one claim
  get_adjuster_note_history(claim_number) — every adjuster note on that claim

Auth: requires CLAIMS_SYSTEM_TOKEN in the process environment. The token is
not scoped per claim — any holder can read any claim in the data store.

Logging: one JSON line per tools/call on stderr, containing the timestamp,
tool, claim_number, outcome and the last 4 characters of the token.
"""

import datetime
import json
import os
import pathlib
import re
import sys

SERVER_INFO       = {"name": "claims-system", "version": "0.4.2"}
PROTOCOL_VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05"]
DATA_PATH = pathlib.Path(os.environ.get(
    "CLAIMS_SYSTEM_DATA",
    pathlib.Path(__file__).resolve().parent.parent / "data" / "claims_system.json",
))
CLAIM_RE = re.compile(r"^CLM-\d{4}-\d{5}$")


def _audit(tool: str, claim_number, outcome: str):
    token = os.environ.get("CLAIMS_SYSTEM_TOKEN", "")
    print(json.dumps({
        "ts": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "server": "claims-system", "tool": tool, "claim_number": claim_number,
        "outcome": outcome, "token_suffix": token[-4:] if token else None,
    }), file=sys.stderr, flush=True)


def _store():
    """Read the claims store. Raises OSError if the backend is unreachable."""
    return json.loads(DATA_PATH.read_text())


def _lookup(claim_number):
    """Return (claim, None) or (None, recoverable_error_text)."""
    if not os.environ.get("CLAIMS_SYSTEM_TOKEN"):
        return None, ("unauthorized: no CLAIMS_SYSTEM_TOKEN was provided to the "
                      "claims-system server. This is a configuration problem, not "
                      "a problem with the claim number; do not retry.")
    if not isinstance(claim_number, str) or not CLAIM_RE.match(claim_number.strip()):
        return None, (f"claim number {claim_number!r} is malformed: claim numbers look "
                      f"like CLM-YYYY-nnnnn (e.g. CLM-2024-88102). Ask the user to "
                      f"confirm the number; do not guess one.")
    try:
        store = _store()
    except OSError as e:
        return None, (f"claims system unavailable ({e.__class__.__name__}). The claim "
                      f"number may be valid; retry later and do not answer about "
                      f"coverage without the claim loaded.")
    claim = store.get(claim_number.strip())
    if claim is None:
        return None, (f"claim {claim_number} not found: the number is well-formed but "
                      f"no claim with that number exists. Ask the user to check it for "
                      f"a typo; do not answer about coverage with no claim loaded.")
    return claim, None


TOOLS = {
    "get_claim_status": {
        "description": (
            "Get the current status of one insurance claim by claim number: status "
            "(pending, under_review, escalated, approved, partial_settlement, denied), "
            "claimant, policy number, declared damage type, declared amount, policy "
            "excess, submitted date, last update and assigned adjuster. "
            "Claim numbers look like CLM-YYYY-nnnnn. Does not return adjuster notes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"claim_number": {
                "type": "string", "pattern": r"^CLM-\d{4}-\d{5}$",
                "description": "Claim number, e.g. CLM-2024-88102."}},
            "required": ["claim_number"],
        },
    },
    "get_adjuster_note_history": {
        "description": (
            "Get the full adjuster note history for one claim, oldest first. Each "
            "entry has date, stage (pending, reviewed, escalated, denied), adjuster "
            "and the note text. Later stages supersede earlier ones: an 'escalated' "
            "note confirms or overturns a provisional 'reviewed' finding. "
            "Claim numbers look like CLM-YYYY-nnnnn."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"claim_number": {
                "type": "string", "pattern": r"^CLM-\d{4}-\d{5}$",
                "description": "Claim number, e.g. CLM-2024-88102."}},
            "required": ["claim_number"],
        },
    },
}


def call_tool(name: str, args: dict):
    claim_number = args.get("claim_number")
    claim, err = _lookup(claim_number)
    if err:
        _audit(name, claim_number, "error")
        return err, True
    if name == "get_claim_status":
        out = {k: v for k, v in claim.items() if k not in ("note_history", "legacy_id")}
    else:
        out = {"claim_number": claim["claim_number"], "notes": claim["note_history"]}
    _audit(name, claim_number, "ok")
    return json.dumps(out), False


# ── JSON-RPC / MCP plumbing ───────────────────────────────────────────────────

def _send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def handle(msg):
    method, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if method == "initialize":
        asked = params.get("protocolVersion")
        _send({"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
        }})
    elif method == "notifications/initialized":
        pass
    elif method == "ping":
        _send({"jsonrpc": "2.0", "id": mid, "result": {}})
    elif method == "tools/list":
        _send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [
            {"name": n, **t} for n, t in TOOLS.items()]}})
    elif method == "tools/call":
        name = params.get("name")
        if name not in TOOLS:
            _send({"jsonrpc": "2.0", "id": mid,
                   "error": {"code": -32602, "message": f"Unknown tool: {name}"}})
            return
        text, is_error = call_tool(name, params.get("arguments") or {})
        _send({"jsonrpc": "2.0", "id": mid, "result": {
            "content": [{"type": "text", "text": text}], "isError": is_error}})
    elif "id" in msg:
        _send({"jsonrpc": "2.0", "id": mid,
               "error": {"code": -32601, "message": f"Method not found: {method}"}})


def main():
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            _send({"jsonrpc": "2.0", "id": None,
                   "error": {"code": -32700, "message": "Parse error"}})
            continue
        handle(msg)


if __name__ == "__main__":
    main()
