import hashlib
import importlib
import math
import re
from functools import lru_cache
from typing import List, Optional, Any
from app.config import EMBEDDING_MODEL_NAME, EMBEDDING_DIMENSION

_sentence_transformers_mod: Optional[Any] = None
try:
    _sentence_transformers_mod = importlib.import_module("sentence_transformers")
except Exception:
    _sentence_transformers_mod = None


@lru_cache(maxsize=1)
def get_embedder():
    """Load and cache the SentenceTransformer model if installed."""
    if _sentence_transformers_mod is not None:
        try:
            SentenceTransformer = getattr(_sentence_transformers_mod, "SentenceTransformer")
            return SentenceTransformer(EMBEDDING_MODEL_NAME)
        except Exception:
            return None
    return None


class FeatureHashEmbedder:
    """Zero-dependency deterministic 384-dimensional unigram+bigram feature hashing embedder fallback."""

    def __init__(self, dim: int = EMBEDDING_DIMENSION):
        self.dim = dim

    def encode_text(self, text: str) -> List[float]:
        cleaned = text.lower().replace("application", "app")
        # Preserve alphanumeric, underscores, and hyphens for package names and code symbols
        tokens = re.findall(r'[a-zA-Z0-9_\-]+', cleaned)
        if not tokens:
            return [0.0] * self.dim

        vec = [0.0] * self.dim

        # 1. Unigram feature hashing
        for token in tokens:
            md5_hash = hashlib.md5(token.encode('utf-8')).hexdigest()
            idx = int(md5_hash, 16) % self.dim
            vec[idx] += 1.0

        # 2. Bigram feature hashing for phrase matching
        for i in range(len(tokens) - 1):
            bigram = f"{tokens[i]}_{tokens[i+1]}"
            md5_hash = hashlib.md5(bigram.encode('utf-8')).hexdigest()
            idx = int(md5_hash, 16) % self.dim
            vec[idx] += 1.5

        # L2 Normalize
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0:
            vec = [x / norm for x in vec]
        return vec

    def encode_batch(self, texts: List[str]) -> List[List[float]]:
        return [self.encode_text(t) for t in texts]


_fallback_embedder = FeatureHashEmbedder()


def embed_texts(texts: List[str]) -> List[List[float]]:
    """Generate dense embeddings for a list of text strings."""
    if _sentence_transformers_mod is not None:
        try:
            embedder = get_embedder()
            if embedder:
                embeddings = embedder.encode(texts, normalize_embeddings=True)
                return embeddings.tolist()
        except Exception:
            pass

    return _fallback_embedder.encode_batch(texts)


def embed_query(text: str) -> List[float]:
    """Generate a dense embedding vector for a single query string."""
    return embed_texts([text])[0]
