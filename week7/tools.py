"""
Week 7 — Insurance claims tools.

Three tools used by both the agent and the fixed workflow:
  1. get_claim             — pull the raw claim record
  2. search_policy_exclusions — look up exclusions for a damage cause
  3. get_adjuster_notes    — NEW: read adjuster notes filtered by review stage

The third tool uses an enum for the claim-status (review-stage) parameter and
has exactly one job: fetch adjuster notes. It does not overlap with get_claim
(which returns structured claim metadata) or search_policy_exclusions (which
returns exclusion rules from the policy).
"""

import json
import pathlib

_CLAIMS_FILE = pathlib.Path(__file__).parent / "claims.jsonl"

# ── Policy exclusions database ────────────────────────────────────────────────
# Keyed by damage CAUSE (not declared claim type).
# An agent must look up the ACTUAL cause found in adjuster notes; a fixed
# workflow that always uses the declared type will miss entries like "flood"
# when the declared type is "water_damage".
POLICY_DB = {
    "water_damage": {
        "exclusions": ["pre_existing_damage", "intentional_damage", "gradual_seepage"],
        "description": (
            "Covers sudden and accidental water damage from internal sources only "
            "(e.g. burst pipes, appliance failure). External flooding is NOT covered "
            "under this category — see 'flood' for the applicable exclusion."
        ),
        "payout_modifier": 1.0,
        "sub_limit": 8100.0,
    },
    "flood": {
        "exclusions": ["all_flood_damage"],
        "description": (
            "All flood damage is excluded under the standard policy (Clause 4.2b). "
            "A separate flood-insurance rider is required. Payout modifier: 0."
        ),
        "payout_modifier": 0.0,
        "sub_limit": 0.0,
    },
    "fire": {
        "exclusions": ["electrical_fault_without_maintenance", "unoccupied_property_90days"],
        "description": (
            "Covers accidental fire damage. Criminal-act fires (arson) are handled "
            "under the 'arson' cause — look that up separately if arson is suspected."
        ),
        "payout_modifier": 1.0,
        "sub_limit": 100000.0,
    },
    "arson": {
        "exclusions": ["arson_and_criminal_acts"],
        "description": (
            "No coverage for fire caused by arson or any criminal act, whether by "
            "the insured or a third party (Clause 8.1). Payout modifier: 0."
        ),
        "payout_modifier": 0.0,
        "sub_limit": 0.0,
    },
    "theft": {
        "exclusions": ["unattended_vehicle_key_in", "negligent_security"],
        "description": (
            "Covers theft of insured property. Excludes cases of negligent security "
            "or vehicle theft where keys were left unattended inside."
        ),
        "payout_modifier": 1.0,
        "sub_limit": 50000.0,
    },
    "hail_damage": {
        "exclusions": ["cosmetic_damage_only"],
        "description": (
            "Covers structural damage from hail. Purely cosmetic dents with no "
            "functional impact are excluded."
        ),
        "payout_modifier": 1.0,
        "sub_limit": 30000.0,
    },
    "vandalism": {
        "exclusions": ["tenant_vandalism"],
        "description": (
            "Covers third-party vandalism to insured property. Damage caused by "
            "the insured's own tenants is excluded."
        ),
        "payout_modifier": 1.0,
        "sub_limit": 10000.0,
    },
    "vehicle_accident": {
        "exclusions": ["racing_and_competitions", "commercial_use_without_endorsement"],
        "description": (
            "Covers accidental vehicle damage. Excludes racing and commercial use "
            "without a commercial-use endorsement. Intoxicated-driving incidents "
            "must be looked up under 'intoxicated_driving'."
        ),
        "payout_modifier": 1.0,
        "sub_limit": 50000.0,
    },
    "intoxicated_driving": {
        "exclusions": ["all_dui_incidents"],
        "description": (
            "Any accident where the driver's BAC exceeded the legal limit is fully "
            "excluded (Clause 6.3). Payout modifier: 0."
        ),
        "payout_modifier": 0.0,
        "sub_limit": 0.0,
    },
    "tree_damage": {
        "exclusions": ["diseased_tree_known_risk"],
        "description": (
            "Covers storm-related tree-fall damage. Excludes damage from trees "
            "the insured knew to be diseased or structurally unsound."
        ),
        "payout_modifier": 1.0,
        "sub_limit": 15000.0,
    },
    "lightning": {
        "exclusions": ["power_surge_without_suppressor"],
        "description": (
            "Covers direct lightning strike damage. Sub-limit of £2,300 per "
            "electronics event. Surge damage without a whole-home suppressor installed "
            "is excluded."
        ),
        "payout_modifier": 1.0,
        "sub_limit": 2300.0,
    },
    "medical": {
        "exclusions": ["pre_existing_conditions"],
        "description": "Covers medical expenses. Excludes claims related to conditions pre-dating the policy.",
        "payout_modifier": 1.0,
        "sub_limit": 25000.0,
    },
}

VALID_CAUSES = list(POLICY_DB.keys())
VALID_STATUSES = ["pending", "reviewed", "escalated", "denied"]


def _load_claims() -> dict:
    claims = {}
    with open(_CLAIMS_FILE) as f:
        for line in f:
            if line.strip():
                c = json.loads(line)
                claims[c["claim_id"]] = c
    return claims


_CLAIMS_CACHE = None  # type: dict


def _claims() -> dict:
    global _CLAIMS_CACHE
    if _CLAIMS_CACHE is None:
        _CLAIMS_CACHE = _load_claims()
    return _CLAIMS_CACHE


# ── Tool 1: get_claim ─────────────────────────────────────────────────────────

def get_claim(claim_id: str) -> dict:
    """
    Retrieve the raw claim record for a given claim ID.
    Returns the claimant name, declared damage type, claimed amount,
    policy excess, policy number, and submission date.
    Does NOT include adjuster notes or policy exclusion details.
    """
    c = _claims().get(claim_id)
    if c is None:
        return {"error": f"Claim {claim_id!r} not found."}
    return {
        "claim_id":         c["claim_id"],
        "claimant":         c["claimant"],
        "declared_type":    c["declared_type"],
        "declared_amount":  c["declared_amount"],
        "policy_excess":    c["policy_excess"],
        "policy_number":    c["policy_number"],
        "submission_date":  c["submission_date"],
    }


# ── Tool 2: search_policy_exclusions ─────────────────────────────────────────

def search_policy_exclusions(cause: str) -> dict:
    """
    Look up the exclusion rules that apply to a specific damage cause.
    Returns the list of applicable exclusions, a payout modifier (0.0 = full
    denial, 1.0 = full coverage), and any sub-limit on the payout.
    The cause parameter must be the ACTUAL damage cause from adjuster
    investigation — a 'water_damage' claim may resolve to 'flood' after
    field review.
    """
    if cause not in POLICY_DB:
        return {
            "error": f"Unknown cause {cause!r}. Valid causes: {VALID_CAUSES}"
        }
    row = POLICY_DB[cause]
    return {
        "cause":            cause,
        "exclusions":       row["exclusions"],
        "description":      row["description"],
        "payout_modifier":  row["payout_modifier"],
        "sub_limit":        row["sub_limit"],
    }


# ── Tool 3 (NEW): get_adjuster_notes ─────────────────────────────────────────

def get_adjuster_notes(claim_id: str, status: str) -> dict:
    """
    Retrieve adjuster notes for a specific claim, filtered by review stage.

    Each stage captures findings from that phase of the claims process:
      pending   — initial intake log: basic details, adjuster assigned
      reviewed  — field-investigator findings, including actual damage cause
                  and any red flags that may trigger a different exclusion
      escalated — senior adjuster comments when the claim needs further scrutiny
      denied    — formal denial rationale once a decision has been made

    Returns an empty 'notes' string if no entry exists for that stage.
    This tool has exactly one job: fetch adjuster notes. It does not return
    claim metadata (use get_claim) or exclusion rules (use search_policy_exclusions).
    """
    if status not in VALID_STATUSES:
        return {
            "error": (
                f"Invalid status {status!r}. "
                f"Must be one of: {VALID_STATUSES}"
            )
        }
    c = _claims().get(claim_id)
    if c is None:
        return {"error": f"Claim {claim_id!r} not found."}
    notes_text = c.get("adjuster_notes", {}).get(status, "")
    return {
        "claim_id": claim_id,
        "status":   status,
        "notes":    notes_text,
    }


# ── OpenAI-format tool schemas ────────────────────────────────────────────────

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_claim",
            "description": (
                "Retrieve the raw claim record for a given claim ID. "
                "Returns the claimant name, declared damage type, claimed amount, "
                "policy excess, policy number, and submission date. "
                "Does not include adjuster notes or policy exclusion details."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "claim_id": {
                        "type": "string",
                        "description": "The unique claim identifier (e.g. CLM-001).",
                    }
                },
                "required": ["claim_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_policy_exclusions",
            "description": (
                "Look up which exclusion rules apply to a specific damage cause. "
                "Returns the list of applicable exclusions, a payout modifier "
                "(0.0 = fully excluded, 1.0 = fully covered), and a sub-limit. "
                "Use the ACTUAL damage cause identified by adjuster investigation "
                "— it may differ from the declared claim type "
                "(e.g. 'water_damage' declared but 'flood' confirmed in the notes)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "cause": {
                        "type": "string",
                        "enum": VALID_CAUSES,
                        "description": (
                            "The damage cause to look up. "
                            "Use the cause found in adjuster notes, not necessarily "
                            "the declared claim type."
                        ),
                    }
                },
                "required": ["cause"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_adjuster_notes",
            "description": (
                "Retrieve adjuster notes for a specific claim, filtered by review stage. "
                "Each stage captures findings from that phase of the claims process: "
                "'pending' = initial intake log; "
                "'reviewed' = field-investigator findings including actual damage cause "
                "and any red flags that may override the declared claim type; "
                "'escalated' = senior adjuster comments for cases needing further scrutiny; "
                "'denied' = formal denial rationale. "
                "Returns an empty notes string if no entry exists for that stage. "
                "This tool has exactly one job: fetch adjuster notes. "
                "It does not return claim metadata (use get_claim) or "
                "exclusion rules (use search_policy_exclusions)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "claim_id": {
                        "type": "string",
                        "description": "The unique claim identifier.",
                    },
                    "status": {
                        "type": "string",
                        "enum": VALID_STATUSES,
                        "description": (
                            "The review stage to retrieve notes for. "
                            "Start with 'reviewed' to get field-investigation findings, "
                            "which include the confirmed damage cause."
                        ),
                    },
                },
                "required": ["claim_id", "status"],
            },
        },
    },
]


def dispatch_tool(name: str, args: dict) -> str:
    """Execute a tool call by name and return the result as a JSON string."""
    if name == "get_claim":
        return json.dumps(get_claim(**args))
    if name == "search_policy_exclusions":
        return json.dumps(search_policy_exclusions(**args))
    if name == "get_adjuster_notes":
        return json.dumps(get_adjuster_notes(**args))
    return json.dumps({"error": f"Unknown tool: {name!r}"})
