"""
Week 11 — retrieval over the policy wording (week9/data/policy_document.md).

BM25 over clause sections; returns the top-k clause ids with scores. Local and
deterministic: no API call, so this stage costs $0 in model spend.
"""

import math
import pathlib
import re
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOC  = ROOT / "week9" / "data" / "policy_document.md"

RETRIEVAL_VERSION = "bm25-k3-v1"
TOP_K = 3

_STOP = set("""a an the of to in on for and or is are was were be been it its this that
with as at by from we our you your any not no do does did if has have had will would
can could should what which who whom how why when where there their they them i my me
""".split())


def _tok(s: str):
    return [t for t in re.findall(r"[a-z0-9]+", s.lower()) if t not in _STOP]


def load_clauses() -> dict:
    clauses, cur = {}, None
    for line in DOC.read_text().splitlines():
        m = re.match(r"^## (\S+) (.+)$", line)
        if m:
            cur = m.group(1)
            clauses[cur] = {"id": cur, "title": m.group(2), "text": ""}
        elif cur and line.strip():
            clauses[cur]["text"] += line.strip() + " "
    for c in clauses.values():
        c["text"] = c["text"].strip()
    return clauses


CLAUSES = load_clauses()
_DOCS   = {cid: _tok(c["title"] + " " + c["text"]) for cid, c in CLAUSES.items()}
_AVGDL  = sum(len(d) for d in _DOCS.values()) / len(_DOCS)
_DF     = Counter(t for d in _DOCS.values() for t in set(d))
_N      = len(_DOCS)


def retrieve(query: str, k: int = TOP_K, k1: float = 1.5, b: float = 0.75):
    """Return [(clause_id, score)] for the top-k clauses, best first."""
    q = _tok(query)
    scored = []
    for cid, d in _DOCS.items():
        tf, s = Counter(d), 0.0
        for t in q:
            if t not in tf:
                continue
            idf = math.log(1 + (_N - _DF[t] + 0.5) / (_DF[t] + 0.5))
            s += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * len(d) / _AVGDL))
        scored.append((cid, round(s, 3)))
    scored.sort(key=lambda x: (-x[1], x[0]))
    return scored[:k]


# ── v2: exclusion-aware retrieval (release coverage-v1.1) ─────────────────────
# Diagnosis from trace tr_044698c89a65: BM25 returned the cover clause 4.2a
# (storm) but not the exclusion 4.2b (flood) that limits it; claim lookups
# retrieved the same 1.1/1.2/8.1 for every claim because the query words
# ("claim", "covered", "payable") match generic clauses.
#
# v2 changes, retrieval layer only:
#   1. generic, non-discriminating words are not scored
#   2. claim lookups add the claim's declared type and its adjuster notes to
#      the retrieval query, and always include that type's cover clause
#   3. every cover clause in context brings the exclusion clauses that modify it

RETRIEVAL_VERSION_V2 = "bm25-k3-v2-excl"

_GENERIC = {"claim", "claims", "cover", "covered", "covers", "payable", "pay", "paid",
            "payout", "policy", "insured", "damage", "much", "amount"}

EXCLUSION_CLAUSES = {"4.2b", "6.3", "8.1"}

# cover clause -> exclusion clauses that modify it (from the policy wording:
# 4.2b "applies whether the claim is declared as escape of water or otherwise",
# 6.3 "applies to all accident claims", 8.1 excludes fire caused by arson)
EXCLUSION_PAIRS = {"3.1": ["4.2b"], "4.2a": ["4.2b"], "5.1": ["8.1"], "6.1": ["6.3"]}

# declared claim type -> cover clause
CAUSE_CLAUSE = {
    "water_damage": "3.1", "flood": "4.2b", "fire": "5.1", "arson": "8.1",
    "theft": "7.1", "hail_damage": "7.4", "vandalism": "7.5",
    "vehicle_accident": "6.1", "intoxicated_driving": "6.3",
    "tree_damage": "7.6", "lightning": "7.7", "medical": "9.1",
}


def _score(q_tokens, k1=1.5, b=0.75):
    scored = []
    for cid, d in _DOCS.items():
        tf, s = Counter(d), 0.0
        for t in q_tokens:
            if t not in tf:
                continue
            idf = math.log(1 + (_N - _DF[t] + 0.5) / (_DF[t] + 0.5))
            s += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * len(d) / _AVGDL))
        scored.append((cid, round(s, 3)))
    scored.sort(key=lambda x: (-x[1], x[0]))
    return scored


def retrieve_v2(question: str, claim: dict = None, k: int = TOP_K) -> dict:
    """
    Returns {"context_ids", "retrieved", "scores", "added": {id: reason}, "query_sources"}.
    claim is the claim record (declared_type, note_history) for claim lookups.
    """
    query, sources = question, ["question"]
    if claim:
        notes = " ".join(n["note"] for n in claim.get("note_history", []))
        query += " " + claim.get("declared_type", "").replace("_", " ") + " " + notes
        sources += ["claim.declared_type", "claim.notes"]
    q = [t for t in _tok(query) if t not in _GENERIC]
    hits = [h for h in _score(q) if h[1] > 0][:k]
    ids, added = [c for c, _ in hits], {}
    if claim and CAUSE_CLAUSE.get(claim.get("declared_type")) not in ids:
        cover = CAUSE_CLAUSE.get(claim.get("declared_type"))
        if cover:
            ids.append(cover)
            added[cover] = f"cover clause for declared type {claim['declared_type']}"
    for cid in list(ids):
        for ex in EXCLUSION_PAIRS.get(cid, []):
            if ex not in ids:
                ids.append(ex)
                added[ex] = f"exclusion that modifies {cid}"
    return {"context_ids": ids, "retrieved": [c for c, _ in hits],
            "scores": [s for _, s in hits], "added": added, "query_sources": sources}
