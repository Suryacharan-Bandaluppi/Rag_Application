import importlib.util
import logging
import math
import re
from typing import List, Dict, Any, Optional
from app.config import (
    VECTOR_STORE_TYPE,
    PINECONE_API_KEY,
    PINECONE_INDEX_NAME,
    PINECONE_CLOUD,
    PINECONE_REGION,
    EMBEDDING_DIMENSION,
    CHROMA_DB_DIR,
    CHROMA_COLLECTION_NAME,
)

logger = logging.getLogger("payslate_vectorstore")

HAS_PINECONE = importlib.util.find_spec("pinecone") is not None
HAS_CHROMADB = importlib.util.find_spec("chromadb") is not None


class InMemoryVectorStore:
    """Fallback zero-dependency in-memory vector store with hybrid keyword boosting."""

    def __init__(self):
        self.vectors = []

    def upsert(self, ids: List[str], vectors: List[List[float]], metadatas: List[Dict[str, Any]], documents: List[str]):
        for doc_id, vec, meta, doc in zip(ids, vectors, metadatas, documents):
            self.vectors.append({
                "id": doc_id,
                "vector": vec,
                "metadata": meta or {},
                "text": doc or ""
            })

    def query(self, query_vector: List[float], top_k: int = 5, query_text: str = "", use_keyword_boost: bool = True) -> List[Dict[str, Any]]:
        def cosine_similarity(v1, v2):
            dot = sum(a * b for a, b in zip(v1, v2))
            norm1 = math.sqrt(sum(a * a for a in v1))
            norm2 = math.sqrt(sum(b * b for b in v2))
            return dot / (norm1 * norm2) if (norm1 and norm2) else 0.0

        # Extract keyword/symbol terms from query text
        query_terms = []
        if query_text:
            query_terms = [
                t.lower() for t in re.findall(r'[a-zA-Z0-9_\-]+', query_text)
                if len(t) > 2 and t.lower() not in {"what", "is", "the", "version", "used", "for", "how", "does", "about", "are"}
            ]

        scored = []
        for item in self.vectors:
            vec_score = cosine_similarity(query_vector, item["vector"])
            doc_text_lower = item["text"].lower()

            # Hybrid keyword/symbol match boost
            boost = 0.0
            if use_keyword_boost:
                for term in query_terms:
                    if term in doc_text_lower:
                        boost += 0.35  # Strong boost for exact package or symbol presence

            final_score = vec_score + boost

            scored.append({
                "id": item["id"],
                "score": final_score,
                "source": item["metadata"].get("source", "Unknown"),
                "text": item["text"]
            })
        
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]

    def count(self) -> int:
        return len(self.vectors)

    def reset(self):
        self.vectors = []


class VectorStoreAdapter:
    """Unified interface for Pinecone (Cloud), ChromaDB (Local), and InMemory (Fallback)."""

    def __init__(self):
        self.store_type = VECTOR_STORE_TYPE
        self._init_store()

    def _init_store(self):
        # 1. Try Pinecone if requested & configured
        if self.store_type == "pinecone" and PINECONE_API_KEY and HAS_PINECONE:
            try:
                pinecone_mod = importlib.import_module("pinecone")
                Pinecone = getattr(pinecone_mod, "Pinecone")
                ServerlessSpec = getattr(pinecone_mod, "ServerlessSpec")

                pc = Pinecone(api_key=PINECONE_API_KEY)
                existing = pc.list_indexes().names()
                if PINECONE_INDEX_NAME not in existing:
                    pc.create_index(
                        name=PINECONE_INDEX_NAME,
                        dimension=EMBEDDING_DIMENSION,
                        metric="cosine",
                        spec=ServerlessSpec(cloud=PINECONE_CLOUD, region=PINECONE_REGION),
                    )
                self.index = pc.Index(PINECONE_INDEX_NAME)
                logger.info("Initialized Pinecone index: %s", PINECONE_INDEX_NAME)
                return
            except Exception as e:
                logger.warning("Pinecone init failed (%s). Falling back.", e)

        # 2. Try ChromaDB
        if HAS_CHROMADB:
            try:
                chromadb_mod = importlib.import_module("chromadb")
                PersistentClient = getattr(chromadb_mod, "PersistentClient")
                self.chroma_client = PersistentClient(path=CHROMA_DB_DIR)
                self.collection = self.chroma_client.get_or_create_collection(
                    name=CHROMA_COLLECTION_NAME,
                    metadata={"hnsw:space": "cosine"}
                )
                self.store_type = "chroma"
                logger.info("Initialized ChromaDB at %s", CHROMA_DB_DIR)
                return
            except Exception as e:
                logger.warning("ChromaDB init failed (%s). Using InMemory vector store.", e)

        # 3. Fallback to In-Memory vector store
        self.store_type = "in_memory"
        self.memory_store = InMemoryVectorStore()
        logger.info("Initialized InMemory Vector Store fallback.")

    def upsert(self, ids: List[str], vectors: List[List[float]], metadatas: List[Dict[str, Any]], documents: List[str]):
        if self.store_type == "pinecone":
            to_upsert = [
                {"id": doc_id, "values": vec, "metadata": {**meta, "text": doc}}
                for doc_id, vec, meta, doc in zip(ids, vectors, metadatas, documents)
            ]
            self.index.upsert(vectors=to_upsert)
        elif self.store_type == "chroma":
            self.collection.add(
                ids=ids,
                embeddings=vectors,
                metadatas=metadatas,
                documents=documents,
            )
        else:
            self.memory_store.upsert(ids, vectors, metadatas, documents)

    def query(self, query_vector: List[float], top_k: int = 5, query_text: str = "", use_keyword_boost: bool = True) -> List[Dict[str, Any]]:
        if self.store_type == "pinecone":
            matches = []
            res = self.index.query(vector=query_vector, top_k=top_k, include_metadata=True)
            for m in res.matches:
                meta = m.metadata or {}
                matches.append({
                    "id": m.id,
                    "score": getattr(m, "score", 0.0),
                    "source": meta.get("source", "Unknown"),
                    "text": meta.get("text", "")
                })
            return matches
        elif self.store_type == "chroma":
            matches = []
            res = self.collection.query(
                query_embeddings=[query_vector],
                n_results=top_k,
                include=["metadatas", "documents", "distances"]
            )
            if res and res["ids"] and len(res["ids"][0]) > 0:
                for doc_id, meta, doc, dist in zip(
                    res["ids"][0],
                    res["metadatas"][0],
                    res["documents"][0],
                    res["distances"][0]
                ):
                    matches.append({
                        "id": doc_id,
                        "score": 1.0 - float(dist) if dist is not None else 1.0,
                        "source": meta.get("source", "Unknown") if meta else "Unknown",
                        "text": doc or ""
                    })
            return matches
        else:
            return self.memory_store.query(query_vector=query_vector, top_k=top_k, query_text=query_text, use_keyword_boost=use_keyword_boost)

    def get_stats(self) -> Dict[str, Any]:
        if self.store_type == "chroma":
            return {"type": "chroma", "total_chunks": self.collection.count()}
        elif self.store_type == "pinecone":
            try:
                stats = self.index.describe_index_stats()
                return {"type": "pinecone", "total_chunks": stats.total_vector_count}
            except Exception:
                return {"type": "pinecone", "total_chunks": 0}
        else:
            return {"type": "in_memory", "total_chunks": self.memory_store.count()}

    def reset(self):
        if self.store_type == "chroma":
            self.chroma_client.delete_collection(name=CHROMA_COLLECTION_NAME)
            self.collection = self.chroma_client.create_collection(
                name=CHROMA_COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"}
            )
        elif self.store_type == "pinecone":
            self.index.delete(delete_all=True)
        else:
            self.memory_store.reset()


_vector_store_instance = None


def get_vector_store() -> VectorStoreAdapter:
    global _vector_store_instance
    if _vector_store_instance is None:
        _vector_store_instance = VectorStoreAdapter()
    return _vector_store_instance
