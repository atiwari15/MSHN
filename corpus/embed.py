"""Turn chunks into vectors.

Uses the same local ONNX MiniLM model Chroma bundled (all-MiniLM-L6-v2,
384 dimensions), so the corpus loop still needs no embedding API key.
With pgvector we own the embedding step explicitly rather than letting
the vector store do it implicitly on insert.
"""

from __future__ import annotations

import functools

from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

EMBEDDING_DIM = 384


@functools.lru_cache(maxsize=1)
def _model():
    """Loading the ONNX model is slow; do it once per process."""
    return DefaultEmbeddingFunction()


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    return [list(map(float, v)) for v in _model()(texts)]


def embed_text(text: str) -> list[float]:
    return embed_texts([text])[0]
