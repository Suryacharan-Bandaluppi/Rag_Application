# Bonus — the failed case on the A2A task lifecycle

**AgentCard:** `agent_card.json` (A2A protocol 0.3.0): one skill `triage-claim`, JSON or text in, JSON out, HTTP bearer auth, push notifications on, state-transition history on.

**Mapping CLM-001 with the exclusions worker returning HTTP 500:**

| A2A state | What happened in our run | What should have happened |
|---|---|---|
| `submitted` | "Triage claim CLM-001" received | same |
| `working` | get_claim OK, notes worker OK (flood confirmed), exclusions worker 500 ×3 | same, then stop retrying |
| `completed` | **Actual:** `denied` "per policy exclusions" that were never read | ✗ this ending is the lie |
| `failed` | — | Defensible but wasteful: discards valid findings (flood confirmed) and gives the caller nothing to act on |
| **`input-required`** | — | **The right end state.** Pause with the partial findings as an artifact ("notes confirm flood; exclusions service unavailable; proposed: deny under the flood exclusion") and wait for the adjuster to confirm, or resume when the service is back |

**Should it have ended failed or input-required?** `input-required`, awaiting the adjuster's confirmation. The decision is likely but unverified, and a £15,000 claim should not be denied on an unchecked policy. Nor should the evidence gathered so far be thrown away as a terminal `failed`.

**What A2A buys over a plain REST call to the worker (two lines):**
A REST 500 has one channel, so the calling LLM is left to paper over it, which is exactly how we got "denied per policy exclusions". A2A gives the delegation a task id and explicit states (`input-required`, `failed`, `completed`, with history), so "I can't verify this" becomes a resumable state the orchestrator must handle, not an error string it can talk past.

*Note: this is a design mapping. The orchestrator in this repo does not implement the A2A wire protocol.*
