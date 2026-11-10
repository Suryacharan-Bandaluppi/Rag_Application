"""
Week 9 — capture the raw JSON-RPC exchange with the claims-system server.

Uses the agent's own MCP client and the claims_system entry from
mcp_config.json, and records every line on the wire verbatim:
    initialize -> notifications/initialized -> tools/list -> tools/call

Output: week9/evidence/wire_raw.jsonl (one wire line per row, unedited).
wire.json is built from this file plus hand-written annotations.
"""

import json
import pathlib
import sys

WEEK9 = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(WEEK9.parent))

from week9.agent import load_config, DEFAULT_CONFIG
from week9.mcp_client import StdioMCPClient

SERVER = "claims_system"
OUT    = WEEK9 / "evidence" / "wire_raw.jsonl"


def main():
    spec = load_config(DEFAULT_CONFIG)["mcpServers"][SERVER]
    wire = []
    c = StdioMCPClient(SERVER, spec["command"], spec.get("args", []), env=spec.get("env"),
                       cwd=str(DEFAULT_CONFIG.parent), wire=wire).start()
    try:
        c.initialize()
        c.list_tools()
        c.call_tool("get_claim_status", {"claim_number": "CLM-2024-88102"})
    finally:
        c.close()
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text("".join(json.dumps(w) + "\n" for w in wire))
    for w in wire:
        print(f"{w['direction']:>15}  {w['raw'][:150]}")
    print(f"\n{len(wire)} wire lines -> {OUT}")


if __name__ == "__main__":
    main()
