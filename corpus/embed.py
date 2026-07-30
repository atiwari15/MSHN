"""Turn chunks into vectors.

Uses Chroma's bundled default embedding function (a local ONNX MiniLM
model) so the corpus loop needs no external embedding API key.
"""

from chromadb.utils.embedding_functions import DefaultEmbeddingFunction


def get_embedding_function():
    return DefaultEmbeddingFunction()
