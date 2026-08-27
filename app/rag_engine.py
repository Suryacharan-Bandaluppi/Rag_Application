import json
import logging
import urllib.request
import urllib.error
from typing import Dict, Any, List

from app.config import get_groq_api_key, get_groq_model, TOP_K
from app.embeddings import embed_query
from app.vectorstore import get_vector_store

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


def search_context(query: str, top_k: int = TOP_K) -> List[Dict[str, Any]]:
    """Embed the user query and retrieve top matching document chunks."""
    query_vector = embed_query(query)
    store = get_vector_store()
    return store.query(query_vector=query_vector, top_k=top_k)


def format_context_prompt(matches: List[Dict[str, Any]]) -> str:
    """Format retrieved vector matches into a structured context block for the LLM."""
    if not matches:
        return "No relevant context found."

    context_blocks = []
    for i, match in enumerate(matches, 1):
        source = match.get("source", "Unknown Document")
        text = match.get("text", "").strip()
        context_blocks.append(f"[Text Chunk {i} from File: {source}]\n{text}")

    return "\n\n---\n\n".join(context_blocks)


def call_groq_api(api_key: str, model: str, system_prompt: str, user_prompt: str) -> str:
    """Call Groq API directly using Python standard library (urllib) without external SDK dependencies."""
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

    req = urllib.request.Request(
        url=url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            res_body = json.loads(response.read().decode("utf-8"))
            return res_body["choices"][0]["message"]["content"].strip()
    except urllib.error.HTTPError as e:
        err_text = e.read().decode("utf-8", errors="ignore")
        logger.error("Groq HTTP Error %d: %s", e.code, err_text)
        if e.code == 401:
            raise ValueError("Invalid Groq API Key. Please check your GROQ_API_KEY in `.env`.")
        raise RuntimeError(f"Groq API HTTP {e.code}: {err_text}")
    except Exception as e:
        logger.error("Groq API request failed: %s", e)
        raise e


def answer_query(query: str, top_k: int = TOP_K) -> Dict[str, Any]:
    """Execute RAG pipeline: retrieval, context assembly, Groq LLM inference, and citation extraction."""
    matches = search_context(query, top_k=top_k)
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
            "error": "GROQ_API_KEY_MISSING"
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
        }

    except Exception as e:
        logger.error("Groq API inference failed: %s", e)
        return {
            "answer": f"⚠️ Error from Groq API: {str(e)}",
            "sources": sources,
            "context_retrieved": len(matches),
            "error": str(e)
        }
