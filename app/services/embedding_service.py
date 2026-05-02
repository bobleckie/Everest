"""
Embedding service — generates and stores vector embeddings for document chunks.

Embedding backends (selected by EMBEDDING_BACKEND env var):
    "tfidf"  (default) — sklearn HashingVectorizer + TF-IDF. Fully local, no network,
                         no model download. Great for keyword-heavy docs like RFPs.
    "sbert"            — sentence-transformers (all-MiniLM-L6-v2). Requires HuggingFace
                         access; only use when the model is already cached locally.
    "fallback"         — deterministic hash pseudo-embedding. Dev/testing only.
"""
import os
import json
import logging
import numpy as np
from typing import List, Optional

logger = logging.getLogger(__name__)

# Keep dim at 384 for compatibility with any previously-written embeddings.
EMBEDDING_DIM = 384
EMBEDDING_BACKEND = os.getenv("EMBEDDING_BACKEND", "tfidf").lower()

_sbert_model = None
_sbert_name = "all-MiniLM-L6-v2"

_hashing_vectorizer = None


def _get_sbert_model():
    """Lazy-load sentence-transformers model (only if explicitly requested)."""
    global _sbert_model
    if _sbert_model is not None:
        return _sbert_model
    try:
        from sentence_transformers import SentenceTransformer
        logger.info(f"Loading sentence-transformers model: {_sbert_name}")
        _sbert_model = SentenceTransformer(_sbert_name)
        logger.info("sentence-transformers model loaded")
        return _sbert_model
    except Exception as e:
        logger.warning(f"Failed to load sentence-transformers: {e}. Falling back to TF-IDF.")
        return None


def _get_hashing_vectorizer():
    """Lazy-build a fixed-dim HashingVectorizer (fully local, no downloads)."""
    global _hashing_vectorizer
    if _hashing_vectorizer is not None:
        return _hashing_vectorizer
    from sklearn.feature_extraction.text import HashingVectorizer
    _hashing_vectorizer = HashingVectorizer(
        n_features=EMBEDDING_DIM,
        norm="l2",             # L2-normalize so cosine similarity is just the dot product
        alternate_sign=False,  # keep non-negative features (behaves like TF)
        ngram_range=(1, 2),    # unigrams + bigrams
        lowercase=True,
        stop_words="english",
    )
    logger.info(f"Initialized local HashingVectorizer embedder (dim={EMBEDDING_DIM})")
    return _hashing_vectorizer


def embed_texts(texts: List[str]) -> List[List[float]]:
    """Generate embeddings for a list of texts. Returns list of float vectors of length EMBEDDING_DIM."""
    if not texts:
        return []

    backend = EMBEDDING_BACKEND

    if backend == "sbert":
        model = _get_sbert_model()
        if model is not None:
            vectors = model.encode(texts, show_progress_bar=False, normalize_embeddings=True)
            return [v.tolist() for v in vectors]
        # sbert requested but unavailable → transparently fall through to TF-IDF
        backend = "tfidf"

    if backend == "tfidf":
        vec = _get_hashing_vectorizer()
        # HashingVectorizer returns a sparse matrix (n_texts x EMBEDDING_DIM), already L2-normalized.
        matrix = vec.transform(texts)
        dense = matrix.toarray().astype(np.float32)
        return [row.tolist() for row in dense]

    # "fallback" or unknown backend
    return [_fallback_embed(t) for t in texts]


def embed_text(text: str) -> List[float]:
    """Generate embedding for a single text."""
    return embed_texts([text])[0]


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """Compute cosine similarity between two vectors."""
    a_arr = np.array(a, dtype=np.float32)
    b_arr = np.array(b, dtype=np.float32)
    dot = np.dot(a_arr, b_arr)
    norm = np.linalg.norm(a_arr) * np.linalg.norm(b_arr)
    if norm == 0:
        return 0.0
    return float(dot / norm)


def find_similar_chunks(query_embedding: List[float], chunk_embeddings: List[dict], top_k: int = 10) -> List[dict]:
    """
    Find the most similar chunks given a query embedding.
    chunk_embeddings: list of {chunk_id, embedding: List[float], ...}
    Returns sorted by similarity descending.
    """
    results = []
    for item in chunk_embeddings:
        if item.get("embedding"):
            sim = cosine_similarity(query_embedding, item["embedding"])
            results.append({**item, "similarity": sim})
    results.sort(key=lambda x: x["similarity"], reverse=True)
    return results[:top_k]


def _fallback_embed(text: str) -> List[float]:
    """Simple hash-based pseudo-embedding for testing without sentence-transformers."""
    import hashlib
    # Create a deterministic but distributed vector from text hash
    h = hashlib.sha384(text.encode()).digest()
    vec = [float(b) / 255.0 - 0.5 for b in h]
    # Pad or truncate to EMBEDDING_DIM
    vec = vec[:EMBEDDING_DIM] + [0.0] * max(0, EMBEDDING_DIM - len(vec))
    # Normalize
    norm = sum(x * x for x in vec) ** 0.5
    if norm > 0:
        vec = [x / norm for x in vec]
    return vec
