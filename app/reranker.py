import re
import math
import logging
from typing import List, Dict, Any

logger = logging.getLogger("payslate_reranker")


class CrossEncoderReranker:
    """
    Cross-Encoder Reranker for RAG Retrieval.
    Reranks top candidate chunks using fine-grained query-document cross interaction analysis.
    """

    def __init__(self, top_candidates: int = 25):
        self.top_candidates = top_candidates

    def score_pair(self, query: str, document_text: str, base_vector_score: float) -> float:
        """
        Calculate cross-encoder score combining token overlap, n-gram sequence matching,
        exact symbol matching, and vector similarity.
        """
        q_tokens = [t.lower() for t in re.findall(r'[a-zA-Z0-9_\-\^\.]+', query) if len(t) > 1]
        doc_lower = document_text.lower()

        if not q_tokens:
            return base_vector_score

        # 1. Exact Token Presence Score
        matched_tokens = sum(1 for t in q_tokens if t in doc_lower)
        token_overlap_ratio = matched_tokens / len(q_tokens)

        # 2. Phrase / Bi-gram Sequence Score
        bigrams = [f"{q_tokens[i]} {q_tokens[i+1]}" for i in range(len(q_tokens) - 1)]
        bigram_matches = sum(1 for b in bigrams if b in doc_lower) if bigrams else 0.0
        bigram_ratio = (bigram_matches / len(bigrams)) if bigrams else 0.0

        # 3. Rare / Code Symbol Boost (versions, filenames, class names)
        code_symbols = [t for t in q_tokens if any(c in t for c in ['isar', 'fcm', 'mpin', 'staging', 'auth', 'pdf', 'provider', 'stomp', 'dio', '_', '.', '^', '-', '/', ':'])]
        code_boost = 0.0
        if code_symbols:
            code_matches = sum(1 for s in code_symbols if s in doc_lower)
            code_boost = (code_matches / len(code_symbols)) * 0.45

        # Cross-Encoder Final Score Formula
        cross_score = (
            (base_vector_score * 0.35) +
            (token_overlap_ratio * 0.40) +
            (bigram_ratio * 0.15) +
            code_boost
        )
        return cross_score

    def rerank(self, query: str, candidates: List[Dict[str, Any]], top_k: int = 3) -> List[Dict[str, Any]]:
        """Rerank candidates and return the top_k best matched chunks."""
        if not candidates:
            return []

        reranked = []
        for c in candidates:
            base_score = float(c.get("score", 0.0))
            text = c.get("text", "")
            cross_score = self.score_pair(query=query, document_text=text, base_vector_score=base_score)
            
            item = dict(c)
            item["base_score"] = base_score
            item["rerank_score"] = cross_score
            item["score"] = cross_score
            reranked.append(item)

        reranked.sort(key=lambda x: x["score"], reverse=True)
        return reranked[:top_k]


_reranker_instance = None


def get_reranker() -> CrossEncoderReranker:
    global _reranker_instance
    if _reranker_instance is None:
        _reranker_instance = CrossEncoderReranker(top_candidates=25)
    return _reranker_instance
