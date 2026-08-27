from groq import Groq

from app.config import GROQ_API_KEY, GROQ_MODEL, TOP_K
from app.embeddings import embed_query
from app.vectorstore import get_index

SYSTEM_PROMPT = (
    "You are a document Q&A assistant. Answer ONLY using the provided context. "
    "Every claim in your answer must be traceable to the context below. "
    "If the context does not contain enough information to answer the question, "
    "reply exactly with: \"I don't know based on the provided documents.\" "
    "Never use outside knowledge and never guess."
)

_groq_client = Groq(api_key=GROQ_API_KEY)


def search(query: str, top_k: int = TOP_K):
    vector = embed_query(query)
    index = get_index()
    response = index.query(vector=vector, top_k=top_k, include_metadata=True)
    return response.matches


def build_context(matches) -> str:
    parts = []
    for m in matches:
        meta = m.metadata or {}
        parts.append(f"[Source: {meta.get('source', 'unknown')}]\n{meta.get('text', '')}")
    return "\n\n---\n\n".join(parts)


def answer_query(query: str) -> dict:
    matches = search(query)
    context = build_context(matches)

    completion = _groq_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {query}"},
        ],
        temperature=0,
    )

    answer = completion.choices[0].message.content

    sources = sorted(
        {
            m.metadata.get("source")
            for m in matches
            if m.metadata and m.metadata.get("source")
        }
    )

    return {"answer": answer, "sources": sources}
