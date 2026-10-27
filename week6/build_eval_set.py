"""
Week 6 — Build the 25-case eval set.

Combines:
  - 20 traces from ../traces.jsonl (Week-5 random sample, seed=42)
  - 5 new cases (3 fresh queries + 2 verbatim regression replays of vocabulary-gap failures)

Writes: week6/eval_cases.jsonl
Run once; read the outputs blind, then write labels_25.json before running eval.py.
"""

import json, time, uuid, pathlib
from datetime import datetime, timezone

# ── paths ─────────────────────────────────────────────────────────────────────
ROOT = pathlib.Path(__file__).resolve().parent.parent
TRACES_FILE   = ROOT / "traces.jsonl"
OUT_FILE      = pathlib.Path(__file__).resolve().parent / "eval_cases.jsonl"

# ── Week-5 taxonomy mode assigned to each original trace ──────────────────────
TRACE_MODE = {
    "E14": "corpus_gap",
    "E17": "vocabulary_gap",
    "E18": "corpus_gap",
    "E19": "vocabulary_gap",
    "E21": "header_displacement",
    "E22": "vocabulary_gap",
    "E24": "header_displacement",
    "E25": "corpus_gap",
    "E26": "corpus_gap",
    "E28": "header_displacement",
    "E29": "vocabulary_gap",
    "E30": "corpus_gap",
    "G01": "ok",
    "G02": "ok",
    "G03": "rank_mismatch",
    "G04": "rank_mismatch",
    "G05": "low_floor",
    "G08": "ok",
    "G09": "ok",
    "G10": "low_floor",
}

# ── 5 new cases ───────────────────────────────────────────────────────────────
# N23 and N24 are VERBATIM replays of the two most impactful vocabulary-gap failures
# (TE17 and TE19) to prove the bug is stable, not a one-off.
NEW_CASES = [
    {
        "case_id": "N21",
        "question": "What routing package does PaySlate use for navigation?",
        "mode": "ok",
        "is_regression": False,
    },
    {
        "case_id": "N22",
        "question": "What is the stomp_dart_client package version used in PaySlate?",
        "mode": "ok",
        "is_regression": False,
    },
    {
        "case_id": "N23",   # REGRESSION — exact replay of TE17 vocabulary-gap failure
        "question": "What flavors does PaySlate support and how are they configured?",
        "mode": "vocabulary_gap",
        "is_regression": True,
        "regression_source": "TE17-709823",
    },
    {
        "case_id": "N24",   # REGRESSION — exact replay of TE19 vocabulary-gap failure
        "question": "How does PaySlate handle push notifications when the app is completely closed?",
        "mode": "vocabulary_gap",
        "is_regression": True,
        "regression_source": "TE19-83488f",
    },
    {
        "case_id": "N25",
        "question": "What are the exact API base URLs used by the dev, staging, and production flavors in PaySlate?",
        "mode": "corpus_gap",
        "is_regression": False,
    },
]

SYSTEM_PROMPT = (
    "You are a RAG AI Assistant. Your primary duty is to answer user questions accurately "
    "based ONLY on the provided context below.\n\n"
    "STRICT RULES:\n"
    "1. Answer ONLY using the facts contained in the provided Context.\n"
    "2. Every statement in your answer must be directly backed by the Context.\n"
    "3. If the Context does not contain enough information to answer the question, "
    "reply EXACTLY with: \"I don't know based on the provided documents.\"\n"
    "4. Do NOT use outside knowledge, do NOT guess, and do NOT make up any details.\n"
    "5. Be concise, clear, and professional."
)


def run_new_case(item: dict) -> dict:
    import sys
    sys.path.insert(0, str(ROOT))
    from app.rag_engine   import search_context, call_groq_api
    from app.config       import get_groq_api_key, get_groq_model
    from app.ingestion    import ingest_document
    from app.vectorstore  import get_vector_store

    # Ensure corpus is ingested
    store = get_vector_store()
    if store.get_stats().get("total_chunks", 0) == 0:
        for p in (ROOT / "data" / "payslate_sample_docs").glob("*.pdf"):
            ingest_document(filename=p.name, raw_bytes=p.read_bytes())

    q  = item["question"]
    t0 = time.perf_counter()
    matches = search_context(query=q, top_k=3, use_reranker=True)
    retrieval_ms = (time.perf_counter() - t0) * 1000.0

    chunks = [
        {
            "chunk_id": m.get("id"),
            "source":   m.get("source"),
            "score":    round(float(m.get("score", 0)), 5),
            "text":     m.get("text", "").strip(),
        }
        for m in matches
    ]

    ctx_parts = []
    for i, c in enumerate(chunks, 1):
        ctx_parts.append(
            f"[Chunk {i} | ID: {c['chunk_id']} | File: {c['source']} | Score: {c['score']:.4f}]\n{c['text']}"
        )
    context_text = "\n\n---\n\n".join(ctx_parts) or "No relevant context found."

    t1 = time.perf_counter()
    raw_output = call_groq_api(
        api_key=get_groq_api_key(),
        model=get_groq_model(),
        system_prompt=SYSTEM_PROMPT,
        user_prompt=f"Context:\n{context_text}\n\nQuestion: {q}",
    )
    generation_ms = (time.perf_counter() - t1) * 1000.0

    sources = sorted({c["source"] for c in chunks if c["source"] and c["source"] != "Unknown"})
    return {
        "trace_id":            f"T{item['case_id']}-{uuid.uuid4().hex[:6]}",
        "candidate_id":        item["case_id"],
        "mode":                item["mode"],
        "is_regression":       item.get("is_regression", False),
        "regression_source":   item.get("regression_source"),
        "timestamp":           datetime.now(timezone.utc).isoformat(),
        "question":            q,
        "model":               get_groq_model(),
        "model_params":        {"temperature": 0.0, "top_k_retrieval": 25, "reranker": True},
        "system_prompt_version": "v1.0-strict-grounded",
        "retrieved_chunks":    chunks,
        "retrieval_latency_ms":  round(retrieval_ms, 2),
        "generation_latency_ms": round(generation_ms, 2),
        "raw_output":          raw_output,
        "sources":             sources,
        "generation_error":    None,
        "redaction_confirmed": True,
    }


def main():
    import sys
    sys.path.insert(0, str(ROOT))

    cases = []

    # ── Part 1: load the 20 original traces and attach mode ───────────────────
    with open(TRACES_FILE) as f:
        for line in f:
            if not line.strip():
                continue
            t = json.loads(line)
            cid = t["candidate_id"]
            t["mode"] = TRACE_MODE.get(cid, "unknown")
            t["is_regression"] = False
            t["regression_source"] = None
            cases.append(t)

    print(f"Loaded {len(cases)} original traces.")

    # ── Part 2: generate 5 new cases ─────────────────────────────────────────
    print("Generating 5 new cases via live RAG pipeline...")
    for item in NEW_CASES:
        print(f"  {item['case_id']}: {item['question'][:60]}...")
        trace = run_new_case(item)
        cases.append(trace)
        print(f"    → {trace['raw_output'][:120].replace(chr(10),' ')}")
        time.sleep(0.5)

    # ── Write out ─────────────────────────────────────────────────────────────
    with open(OUT_FILE, "w") as f:
        for c in cases:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    print(f"\nWrote {len(cases)} eval cases to {OUT_FILE}")
    print("\n── Summary by mode ───────────────────────────────────────")
    from collections import Counter
    ctr = Counter(c["mode"] for c in cases)
    for mode, cnt in sorted(ctr.items()):
        print(f"  {mode:<22} {cnt}")

    print("\n── All 25 trace IDs (for labels_25.json) ────────────────")
    for i, c in enumerate(cases, 1):
        print(f"  {i:02d}. {c['trace_id']}  [{c['candidate_id']}]  {c['question'][:55]}")


if __name__ == "__main__":
    main()
