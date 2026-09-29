"""
Week 5 · Module 3 — Trace Generation Script
Generates 20 real traces from the PaySlate RAG pipeline using a seeded random sample.

Usage:
    python3 generate_traces.py

Output:
    traces.jsonl — one JSON object per line, each a complete replayable trace
"""

import json
import random
import time
import pathlib
import uuid
from datetime import datetime, timezone

from app.ingestion import ingest_document
from app.vectorstore import get_vector_store
from app.rag_engine import search_context, call_groq_api
from app.config import get_groq_api_key, get_groq_model

SYSTEM_PROMPT = (
    "You are a RAG AI Assistant. Your primary duty is to answer user questions accurately based ONLY on the provided context below.\n\n"
    "STRICT RULES:\n"
    "1. Answer ONLY using the facts contained in the provided Context.\n"
    "2. Every statement in your answer must be directly backed by the Context.\n"
    "3. If the Context does not contain enough information to answer the question, "
    "reply EXACTLY with: \"I don't know based on the provided documents.\"\n"
    "4. Do NOT use outside knowledge, do NOT guess, and do NOT make up any details.\n"
    "5. Be concise, clear, and professional."
)

# ─── Candidate query pool (30 queries) ───────────────────────────────────────
# Mix of: golden-set questions, architecture queries, out-of-scope questions,
# ambiguous phrasing, multi-hop, partial-coverage topics.
CANDIDATE_QUERIES = [
    # Golden set (Q1–Q12) — known ground truth
    {"id": "G01", "question": "What version of isar is used for local storage in pubspec.yaml?", "category": "edge_case"},
    {"id": "G02", "question": "Which Dart service handles FCM notifications in PaySlate and where is it located?", "category": "edge_case"},
    {"id": "G03", "question": "What package and version are used for biometric unlock on the MPIN screen?", "category": "edge_case"},
    {"id": "G04", "question": "What is the entry point file for the staging flavor in PaySlate?", "category": "edge_case"},
    {"id": "G05", "question": "How does PaySlate handle offline data synchronization when a cash entry is added?", "category": "general"},
    {"id": "G06", "question": "What routing package is used in PaySlate and how are 401 unauthenticated responses handled?", "category": "general"},
    {"id": "G07", "question": "What package is used for generating PDF account statements in PaySlate?", "category": "edge_case"},
    {"id": "G08", "question": "What state management package is being phased out in PaySlate, and what is replacing it?", "category": "general"},
    {"id": "G09", "question": "What class handles route persistence for FCM background refresh decisions?", "category": "edge_case"},
    {"id": "G10", "question": "What are the four checklist steps required before introducing a new package to PaySlate?", "category": "general"},
    {"id": "G11", "question": "What client library is used for real-time WebSocket communication in PaySlate?", "category": "edge_case"},
    {"id": "G12", "question": "How are HTTP requests being migrated in core/network?", "category": "general"},
    # Extended queries (Q13–Q30) — probe failure modes
    {"id": "E13", "question": "How does PaySlate structure its feature folders and what naming convention is used?", "category": "architecture"},
    {"id": "E14", "question": "What is the minimum Dart SDK version required for PaySlate?", "category": "version_lookup"},
    {"id": "E15", "question": "What is PaySlate's strategy for unit testing and what test framework is used?", "category": "out_of_scope"},
    {"id": "E16", "question": "How does PaySlate refresh its authentication tokens when they expire?", "category": "multi_hop"},
    {"id": "E17", "question": "What flavors does PaySlate support and how are they configured?", "category": "general"},
    {"id": "E18", "question": "What is the database schema for the cash entry model in PaySlate?", "category": "out_of_scope"},
    {"id": "E19", "question": "How does PaySlate handle push notifications when the app is completely closed?", "category": "general"},
    {"id": "E20", "question": "What CI/CD pipeline or deployment tool does PaySlate use for releases?", "category": "out_of_scope"},
    {"id": "E21", "question": "What is the purpose of the RouteNameStorage class in PaySlate?", "category": "edge_case"},
    {"id": "E22", "question": "What packages does PaySlate currently use for state management?", "category": "general"},
    {"id": "E23", "question": "How does PaySlate handle network errors and implement retry logic?", "category": "multi_hop"},
    {"id": "E24", "question": "What is the architecture pattern used in PaySlate — Clean Architecture, MVC, or MVVM?", "category": "architecture"},
    {"id": "E25", "question": "What version of Flutter SDK does PaySlate require as a minimum?", "category": "version_lookup"},
    {"id": "E26", "question": "How does PaySlate implement API pagination for large data sets?", "category": "out_of_scope"},
    {"id": "E27", "question": "What package does PaySlate use for dependency injection?", "category": "version_lookup"},
    {"id": "E28", "question": "How does PaySlate implement the offline-first architecture for data synchronization?", "category": "general"},
    {"id": "E29", "question": "What is PaySlate?", "category": "vague"},
    {"id": "E30", "question": "How does PaySlate handle deep links and universal links on iOS and Android?", "category": "out_of_scope"},
]


RANDOM_SEED = 42
N_SAMPLE = 20
OUTPUT_FILE = "traces.jsonl"
SAMPLE_DOCS_DIR = pathlib.Path("data/payslate_sample_docs")


def ensure_docs_ingested():
    store = get_vector_store()
    stats = store.get_stats()
    if stats.get("total_chunks", 0) == 0:
        print("Vector store is empty — ingesting sample docs...")
        for fpath in SAMPLE_DOCS_DIR.glob("*.pdf"):
            ingest_document(filename=fpath.name, raw_bytes=fpath.read_bytes())
        print(f"Ingested. Total chunks: {get_vector_store().get_stats().get('total_chunks')}")
    else:
        print(f"Vector store ready. Total chunks: {stats.get('total_chunks')}")


def format_context(matches):
    if not matches:
        return "No relevant context found."
    parts = []
    for i, m in enumerate(matches, 1):
        parts.append(
            f"[Chunk ID: {m.get('id')} | File: {m.get('source','?')} | Score: {m.get('score',0):.4f}]\n"
            f"{m.get('text','').strip()}"
        )
    return "\n\n---\n\n".join(parts)


def run_trace(query_item: dict, use_reranker: bool = True) -> dict:
    """Run one query through the full RAG pipeline and return a complete trace record."""
    question = query_item["question"]
    trace_id = f"T{query_item['id']}-{uuid.uuid4().hex[:6]}"
    timestamp = datetime.now(timezone.utc).isoformat()

    api_key = get_groq_api_key()
    model = get_groq_model()
    model_params = {"temperature": 0.0, "top_k_retrieval": 25 if use_reranker else 3, "reranker": use_reranker}

    # Retrieval
    t0 = time.perf_counter()
    matches = search_context(query=question, top_k=3, use_reranker=use_reranker)
    retrieval_ms = (time.perf_counter() - t0) * 1000.0

    retrieved_chunks = [
        {"chunk_id": m.get("id"), "source": m.get("source"), "score": round(float(m.get("score", 0)), 5), "text": m.get("text", "").strip()}
        for m in matches
    ]

    context_text = format_context(matches)
    user_message = f"Context:\n{context_text}\n\nQuestion: {question}"

    # LLM call
    raw_output = ""
    generation_error = None
    t1 = time.perf_counter()
    try:
        raw_output = call_groq_api(
            api_key=api_key,
            model=model,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_message,
        )
    except Exception as e:
        generation_error = str(e)
        raw_output = f"ERROR: {e}"
    generation_ms = (time.perf_counter() - t1) * 1000.0

    sources = sorted({c["source"] for c in retrieved_chunks if c["source"] and c["source"] != "Unknown"})

    # NOTE: No claimant names / PII in PaySlate developer-documentation corpus.
    # All identifiers are technical symbols, file paths, and package names.
    # Redaction confirmation: NO personally-identifiable fields exist in these traces.

    return {
        "trace_id": trace_id,
        "candidate_id": query_item["id"],
        "category": query_item["category"],
        "timestamp": timestamp,
        "question": question,
        "model": model,
        "model_params": model_params,
        "system_prompt_version": "v1.0-strict-grounded",
        "retrieved_chunks": retrieved_chunks,
        "retrieval_latency_ms": round(retrieval_ms, 2),
        "generation_latency_ms": round(generation_ms, 2),
        "raw_output": raw_output,
        "sources": sources,
        "generation_error": generation_error,
        "redaction_confirmed": True,
    }


def main():
    ensure_docs_ingested()

    # ── Seeded random sample ──────────────────────────────────────────────────
    rng = random.Random(RANDOM_SEED)
    sampled = rng.sample(CANDIDATE_QUERIES, N_SAMPLE)
    sampled.sort(key=lambda x: x["id"])  # deterministic order for readability

    print(f"\nSeed: {RANDOM_SEED}")
    print("Sampled query IDs:", [q["id"] for q in sampled])
    print(f"\nRunning {N_SAMPLE} traces...\n")

    traces = []
    for i, qitem in enumerate(sampled, 1):
        print(f"  [{i:02d}/{N_SAMPLE}] {qitem['id']}: {qitem['question'][:70]}...")
        trace = run_trace(qitem, use_reranker=True)
        traces.append(trace)
        time.sleep(0.4)  # Respect Groq rate limits

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        for t in traces:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")

    print(f"\nWrote {len(traces)} traces to {OUTPUT_FILE}")
    print("\nSampled IDs (paste into notes.md):")
    for t in traces:
        print(f"  {t['trace_id']} ← {t['candidate_id']}: {t['question'][:60]}")


if __name__ == "__main__":
    main()
