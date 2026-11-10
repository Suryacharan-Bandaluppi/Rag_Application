# Week 9 · Task Set D — Add the claims-system MCP server without touching the agent

**Module:** M5 — MCP, Multi-agent & A2A.
**Model:** `openai/gpt-oss-120b` on Groq, temperature 0.
**Builds on:** the Week 7/8 claims domain (same claims, causes and exclusions).

## Architecture

```
          mcp_config.json   (the ONLY thing that changes when a server is added)
                │
   week9/agent.py  ── MCP HOST: reads config, spawns servers, discovers tools,
   + mcp_client.py     attaches resources, runs the MODEL (Groq), routes tool calls
        │   stdio JSON-RPC 2.0            │   stdio JSON-RPC 2.0
        ▼                                 ▼
 servers/policy_docs_server.py     third_party/claims_system_server.py
 SERVER ONE (ours)                 SERVER TWO (Claims Platform team)
 tools: search_policy_documents,   tools: get_claim_status,
        get_policy_clause                 get_adjuster_note_history
 resource: policy://exclusions-schedule
```

- **The agent hard-codes nothing.** Every tool the model can call comes from `tools/list` at start-up and is exposed as `<server>__<tool>`.
- **The exclusions schedule is a resource, not a tool.** The host reads it with `resources/read` and attaches it to the system prompt, so the model never has to call out for a list it should already have.
- **No model runs inside either server.** Servers expose capabilities; the host runs the model.
- **Standard library only.** This machine's Python is 3.9, and the official `mcp` SDK needs 3.10+, so the stdio transport is implemented directly. Both servers were checked against the **official MCP TypeScript SDK client (v1.32.0)**: initialize handshake, `tools/list`, `resources/list` and `resources/read`, and `tools/call` for both success and error results.

## Deliverables

| Requirement | File | Result |
|---|---|---|
| 1. Add server two by config; one query provably calls it | `config_diff.txt`, `evidence/server2_query_trace.json` | Trace shows `server=claims_system tool=get_claim_status` and `tool=get_adjuster_note_history` |
| 2. Zero lines changed in the agent module | `agent_diff.txt` | `git diff --no-index --exit-code` gives exit code 0; git blob ids identical for `agent.py` and `mcp_client.py` |
| 3. Tool count before → after, from `tools/list` | `tool_counts.md` | **2 → 4** |
| 4. Raw initialize → tools/list → tools/call, annotated | `wire.json` (raw lines in `evidence/wire_raw.jsonl`) | 7 wire lines, every top-level field annotated |
| 5. Docstring as prompt and a recoverable error, before/after | `error_before_after.md` | Grounded recovery **0/3 → 3/3**; model calls **3 → 2** per run |
| 6. Five-line supply-chain risk note | `risk_note.md` | Exactly 5 lines; verdict **don't ship yet** |

### 1–3. Config-only swap

- **Before:** `mcp_config.json` listed only `policy_docs`. Discovery found **2 tools**: `search_policy_documents`, `get_policy_clause`.
- **The change:** I added the `claims_system` block to `mcp_config.json`; `config_diff.txt` is that entire change.
- **After:** discovery found **4 tools**: the 2 above plus `get_claim_status` and `get_adjuster_note_history`.

The query "Claim CLM-2024-88102 … status, latest adjuster notes, does an exclusion apply?" called:
- `claims_system` → `get_claim_status`
- `claims_system` → `get_adjuster_note_history`
- `policy_docs` → `get_policy_clause("8.1")`

The answer is correct: escalated, arson confirmed, Clause 8.1 applies, no payout.

The proof uses a snapshot. The agent module (`agent.py` + `mcp_client.py`) and config were copied to `evidence/server1_snapshot/` while only server one was configured. `agent_diff.txt` compares that snapshot with the final files.

To also prove it in git history, commit in two steps:
1. Commit everything **except** the `claims_system` block in `mcp_config.json`.
2. Add the block and commit again.

Then `git diff HEAD~1 HEAD --stat` shows only `week9/mcp_config.json`.

### 4. Where the model call happens

The model is called only in the host: `week9/agent.py`, `_groq_call()`, which makes an HTTPS request to Groq.
- It runs **before** a `tools/call`, to choose the tool, and **after** it, to read the result.
- There is no model call anywhere on the MCP wire, and none inside either server.
- The client sends `"capabilities": {}` in initialize, so a server cannot even ask the host to run the model for it (no `sampling` capability).

### 5. Docstring as prompt and recoverable error (our server, `get_policy_clause`)

**Before:**
- Docstring: `"Get clause."`
- Error for any unknown id: `"Error 3"`.

**After:**
- The docstring says when to use the tool, and to quote only from it, never from truncated search snippets.
- It gives the exact id format, with letter suffix and without a "Clause" prefix.
- It says what comes back, and what to do on an error.
- The error names the real ids nearest the request, e.g. `clause '4.2' not found: … Ids starting with '4.2': '4.2a' (Storm), '4.2b' (Flood exclusion). Call again with the id that matches the question.`
- There are separate messages for a "Clause " prefix and for a completely unknown id.

**How it was tested:** the same failing call, `get_policy_clause({"clause_id": "4.2"})`, is injected as the model's first turn and executed against the live server, 3 repeats per phase.

| | Before | After |
|---|---|---|
| Fetched the real clause | 3/3 | 3/3 |
| `4.2b` appeared in a tool result before the model used it | **0/3** | **3/3** |
| Tool calls after the error | 2.00 | **1.00** |
| Model calls per run | 3.00 | **2.00** |
| Tokens per run (mean) | 3,067 | **2,427** |

"Recovered 3/3" before does **not** mean the old error was fine:
- Before, the model searched "clause 4.2", got back the **wrong** clause (Lightning 7.7; the search matched the "2" in "GBP 2,300"), and then requested `4.2b` with no tool result supporting it. It most likely guessed from the attached exclusions schedule.
- After, the error message itself hands it the candidates.

An earlier "before" run whose query contained the word "flood" recovered 3/3 through search. The query was giving the topic away, so I dropped the word to isolate the error message. That run is kept as `evidence/error_before_topic_hint.json`. It also showed the model once "quoting" a clause from a 160-character search snippet, which is why the new docstring forbids quoting snippets.

### 6. Risk note: what the audit found

Two findings come from the evidence, not from assumptions:
- **Inherited environment.** The host spawns servers with its full environment, so the third-party server can read our `GROQ_API_KEY`. I verified this by spawning a probe process the same way.
- **Token suffix in logs.** The server logs the last 4 characters of its token on every call (see `logs/claims_system.stderr.log`).

The fix for the environment leak (pass servers an allow-listed environment) belongs in `mcp_client.py`. I deliberately did **not** make it here, because this task requires the agent module to stay byte-identical. It should be the first change after submission.

## The brief's "common mistakes", and how each is avoided

| Mistake | Here |
|---|---|
| Hard-coding the tool list | Tools come only from `tools/list`; the agent contains no tool names (`agent_diff.txt`) |
| LLM call inside an MCP server | Neither server imports or calls a model |
| Exclusions schedule exposed as a tool | It is a **resource**, attached by the app |
| "Error: lookup failed" for unknown claims | Claims server returns distinct, recoverable messages for a malformed number, an unknown claim, a missing token and an unavailable backend |
| Adding the server without asking what it reaches | `risk_note.md`: don't ship until the environment, token scope and logging are fixed |

## How to re-run

```bash
python3 week9/agent.py --discover                  # live tool count
python3 week9/agent.py "What is the status of claim CLM-2024-88102?"
python3 week9/capture_wire.py                      # re-capture raw wire (evidence/wire_raw.jsonl)
python3 week9/error_demo.py after && python3 week9/error_demo.py report
```

`error_demo.py before` reproduces the "before" phase only if `get_policy_clause` is reverted to the old docstring and `"Error 3"` (shown verbatim in `error_before_after.md`).

**Bonus (gateway, scoped token):** not attempted.
