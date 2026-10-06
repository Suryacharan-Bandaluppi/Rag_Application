# Tool count — before -> after (taken from live tools/list, via `python3 week9/agent.py --discover`)

**2 before -> 4 after**

- before (server one only): policy_docs: search_policy_documents, get_policy_clause
- after (server one + two): policy_docs: search_policy_documents, get_policy_clause; claims_system: get_claim_status, get_adjuster_note_history

Resources attached as context (resources/list, not tools): policy://exclusions-schedule, before and after.

Verbatim discovery output, before (`evidence/discover_server1.txt`):
```text
2 tools discovered via tools/list from 1 server(s):
  policy_docs: search_policy_documents, get_policy_clause
resources attached as context: ['policy://exclusions-schedule']
```

Verbatim discovery output, after (`evidence/discover_server1_plus_2.txt`):
```text
4 tools discovered via tools/list from 2 server(s):
  policy_docs: search_policy_documents, get_policy_clause
  claims_system: get_claim_status, get_adjuster_note_history
resources attached as context: ['policy://exclusions-schedule']
```
