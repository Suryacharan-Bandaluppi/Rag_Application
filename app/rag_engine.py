import json
import time
import logging
import urllib.request
import urllib.error
from typing import Dict, Any, List, Optional

from app.config import get_groq_api_key, get_groq_model, TOP_K
from app.embeddings import embed_query
from app.vectorstore import get_vector_store
from app.reranker import get_reranker

logger = logging.getLogger("rag_engine")

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


def search_context(query: str, top_k: int = TOP_K, use_reranker: bool = True) -> List[Dict[str, Any]]:
    """Embed query, retrieve candidate chunks, and optionally apply Cross-Encoder reranking."""
    query_vector = embed_query(query)
    store = get_vector_store()

    if use_reranker:
        # Step 1: Fetch top 25 candidate chunks via pure dense similarity
        candidates = store.query(query_vector=query_vector, top_k=25, query_text=query, use_keyword_boost=False)
        # Step 2: Apply Cross-Encoder reranking to pick top_k
        reranker = get_reranker()
        return reranker.rerank(query=query, candidates=candidates, top_k=top_k)
    else:
        # Baseline pure dense vector retrieval without reranking
        return store.query(query_vector=query_vector, top_k=top_k, query_text=query, use_keyword_boost=False)


def format_context_prompt(matches: List[Dict[str, Any]]) -> str:
    """Format retrieved vector matches into a structured context block for the LLM."""
    if not matches:
        return "No relevant context found."

    context_blocks = []
    for i, match in enumerate(matches, 1):
        source = match.get("source", "Unknown Document")
        chunk_id = match.get("id", f"chunk-{i}")
        score = match.get("score", 0.0)
        text = match.get("text", "").strip()
        context_blocks.append(f"[Chunk ID: {chunk_id} | File: {source} | Score: {score:.3f}]\n{text}")

    return "\n\n---\n\n".join(context_blocks)


def call_groq_api(api_key: str, model: str, system_prompt: str, user_prompt: str) -> str:
    """Call Groq API directly using Python standard library (urllib)."""
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "RAG-App/1.0"
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.0,
    }

    for attempt in range(5):
        try:
            req = urllib.request.Request(
                url=url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=30) as response:
                res_body = json.loads(response.read().decode("utf-8"))
                return res_body["choices"][0]["message"]["content"].strip()
        except urllib.error.HTTPError as e:
            err_text = e.read().decode("utf-8", errors="ignore")
            if e.code == 429 and attempt < 4:
                sleep_time = (2 ** attempt) + 1.5
                logger.warning(f"Groq API HTTP 429 rate limit. Retrying in {sleep_time:.1f}s (attempt {attempt+1}/5)...")
                time.sleep(sleep_time)
                continue
            logger.error("Groq HTTP Error %d: %s", e.code, err_text)
            if e.code == 401:
                raise ValueError("Invalid Groq API Key. Please check your GROQ_API_KEY in `.env`.")
            raise RuntimeError(f"Groq API HTTP {e.code}: {err_text}")
        except Exception as e:
            logger.error("Groq API request failed: %s", e)
            raise e


def answer_query(query: str, top_k: int = TOP_K, use_reranker: bool = True) -> Dict[str, Any]:
    """Execute RAG pipeline: retrieval, context assembly, Groq LLM inference, and citation extraction."""
    matches = search_context(query, top_k=top_k, use_reranker=use_reranker)
    context_text = format_context_prompt(matches)

    sources = sorted(
        {m.get("source") for m in matches if m.get("source") and m.get("source") != "Unknown"}
    )

    api_key = get_groq_api_key()
    model = get_groq_model()

    if not api_key:
        return {
            "answer": "⚠️ Groq API key is missing or empty. Please paste your key into `GROQ_API_KEY=` inside your `.env` file.",
            "sources": sources,
            "context_retrieved": len(matches),
            "error": "GROQ_API_KEY_MISSING",
            "matches": matches
        }

    try:
        user_message = f"Context:\n{context_text}\n\nQuestion: {query}"
        answer = call_groq_api(
            api_key=api_key,
            model=model,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_message
        )

        return {
            "answer": answer,
            "sources": sources,
            "context_retrieved": len(matches),
            "matches": matches
        }

    except Exception as e:
        logger.error("Groq API inference failed: %s", e)
        return {
            "answer": f"⚠️ Error from Groq API: {str(e)}",
            "sources": sources,
            "context_retrieved": len(matches),
            "error": str(e),
            "matches": matches
        }


def inspect_query(query: str, expected_keywords: Optional[List[str]] = None, top_k: int = 3, use_reranker: bool = True) -> Dict[str, Any]:
    """
    Inspection View logic: Returns Question -> Top-3 Chunks -> Answer -> Failure Tag (R / G / OK / Not-In-Corpus) + 1-line Evidence.
    """
    res = answer_query(query=query, top_k=top_k, use_reranker=use_reranker)
    matches = res.get("matches", [])
    answer = res.get("answer", "")

    # Check if target context is present in top_k matches
    hit_retrieval = False
    evidence_retrieval = ""

    if expected_keywords:
        for m in matches:
            text = m.get("text", "").lower()
            if any(k.lower() in text for k in expected_keywords):
                hit_retrieval = True
                evidence_retrieval = f"Found expected keyword in chunk {m.get('id')}"
                break
    else:
        hit_retrieval = len(matches) > 0

    # Determine failure tag
    if "don't know" in answer.lower() or "not contain enough information" in answer.lower():
        if not hit_retrieval:
            failure_tag = "R"
            evidence = "Retrieval Failure: Top-3 chunks did not contain the target fact."
        else:
            failure_tag = "G"
            evidence = f"Generation Failure: Target chunk was retrieved but LLM returned 'I don't know'."
    elif res.get("error"):
        failure_tag = "G"
        evidence = f"Generation Error: {res.get('error')}"
    else:
        if not hit_retrieval and expected_keywords:
            failure_tag = "R"
            evidence = "Retrieval Failure: Target fact was missing from top-3 chunks, but LLM still answered."
        else:
            failure_tag = "OK"
            evidence = "Success: Correct chunk retrieved and answered by LLM."

    return {
        "query": query,
        "top_3_chunks": matches[:3],
        "answer": answer,
        "sources": res.get("sources", []),
        "failure_tag": failure_tag,
        "evidence": evidence,
        "use_reranker": use_reranker
    }
