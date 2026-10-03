"""
Week 8 — the ONE mitigation: a tighter tool description.

Top failure mode in the baseline trajectory eval: skipped_required_step.
The agent denies CLM-002 for arson after reading only the 'reviewed' notes,
which say the arson exclusion is "under consideration pending investigation
outcome. Senior review recommended." The confirmation is only in the
'escalated' notes, which the agent never opens. Right payout, unsupported path.

Mitigation (from the Week 8 menu: "tighter tool description"):
  Only the get_adjuster_notes description and its `status` parameter
  description change. Same tools, same enum, same system prompt, same budgets,
  no validation, no step limit, no re-planning. One change, so a drop in the
  mode can be attributed to it.

Run `python3 week8/mitigation.py` to print the exact diff against week7.
"""

import copy
import difflib
import json

from week7.tools import TOOL_SCHEMAS

MITIGATED_TOOL_SCHEMAS = copy.deepcopy(TOOL_SCHEMAS)

_notes = next(t for t in MITIGATED_TOOL_SCHEMAS
              if t["function"]["name"] == "get_adjuster_notes")["function"]

_notes["description"] = (
    "Retrieve adjuster notes for a specific claim, filtered by review stage. "
    "Each stage captures findings from that phase of the claims process: "
    "'pending' = initial intake log; "
    "'reviewed' = field-investigator findings including actual damage cause "
    "and any red flags that may override the declared claim type; "
    "'escalated' = senior adjuster comments for cases needing further scrutiny; "
    "'denied' = formal denial rationale. "
    "Returns an empty notes string if no entry exists for that stage. "
    "IMPORTANT: if the 'reviewed' notes describe a finding as provisional — "
    "e.g. 'under consideration', 'pending investigation outcome', "
    "'senior review recommended', 'awaiting' — that finding is NOT confirmed. "
    "Call this tool again with status='escalated' to read the confirmed outcome "
    "before you submit a verdict. Do not deny or approve on a provisional finding. "
    "This tool has exactly one job: fetch adjuster notes. "
    "It does not return claim metadata (use get_claim) or "
    "exclusion rules (use search_policy_exclusions)."
)
_notes["parameters"]["properties"]["status"]["description"] = (
    "The review stage to retrieve notes for. "
    "Start with 'reviewed' to get field-investigation findings, "
    "which include the confirmed damage cause. "
    "Use 'escalated' when the reviewed notes say a finding is still under "
    "consideration or recommend senior review."
)


def diff() -> str:
    before = json.dumps(TOOL_SCHEMAS, indent=2).splitlines()
    after  = json.dumps(MITIGATED_TOOL_SCHEMAS, indent=2).splitlines()
    return "\n".join(difflib.unified_diff(
        before, after, "week7/tools.py TOOL_SCHEMAS",
        "week8/mitigation.py MITIGATED_TOOL_SCHEMAS", lineterm=""))


if __name__ == "__main__":
    print(diff())
