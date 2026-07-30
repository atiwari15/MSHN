"""Vector-store wrapper: incremental add, recency-aware query, expiry."""

from __future__ import annotations

import datetime as dt
import math
import pathlib

import chromadb

from corpus.chunk import Chunk
from corpus.embed import get_embedding_function

DEFAULT_PERSIST_DIR = pathlib.Path(__file__).resolve().parent.parent / "vector_store"
COLLECTION_NAME = "filings"

# How fast relevance decays with filing age, in the recency-aware re-rank.
# A chunk this many days old contributes half its raw similarity weight.
RECENCY_HALF_LIFE_DAYS = 21.0


def get_client(persist_directory: str | pathlib.Path = DEFAULT_PERSIST_DIR):
    return chromadb.PersistentClient(path=str(persist_directory))


def get_collection(client):
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=get_embedding_function(),
        metadata={"hnsw:space": "cosine"},
    )


def add_chunks(collection, chunks: list[Chunk], ticker: str) -> None:
    """Upsert by chunk id, so re-ingesting an already-indexed filing is a
    no-op - the corpus loop should never need to re-embed the whole corpus
    just because it saw a filing again."""
    if not chunks:
        return
    collection.upsert(
        ids=[c.id for c in chunks],
        documents=[c.text for c in chunks],
        metadatas=[{**c.to_metadata(), "ticker": ticker} for c in chunks],
    )


def _age_days(filed_date: str, as_of: dt.date) -> float:
    return max((as_of - dt.date.fromisoformat(filed_date)).days, 0)


def query(
    collection,
    query_text: str,
    ticker: str,
    as_of: str | dt.date,
    top_k: int = 5,
    overfetch: int = 20,
    half_life_days: float = RECENCY_HALF_LIFE_DAYS,
) -> list[dict]:
    """Recency-aware retrieval: over-fetch candidates by semantic similarity,
    then re-rank by similarity * exp(-age / half_life) so an older-but-still-
    similar filing doesn't outrank a fresher, more relevant one.

    `as_of` is the trigger date (the move being explained), not wall-clock
    time - recency is measured relative to when the move happened, which is
    what lets this run identically against historical replay and live data.
    """
    if isinstance(as_of, str):
        as_of = dt.date.fromisoformat(as_of)

    raw = collection.query(
        query_texts=[query_text],
        n_results=overfetch,
        # filed_date <= as_of: a filing that postdates the move being
        # explained can never be the cited cause of it, even if it's
        # semantically similar (ingestion runs ahead of triggers, but
        # this guards against ever leaking future filings into the past).
        where={
            "$and": [
                {"ticker": ticker},
                {"filed_date_int": {"$lte": int(as_of.strftime("%Y%m%d"))}},
            ]
        },
    )
    if not raw["ids"][0]:
        return []

    scored = []
    for doc_id, text, meta, distance in zip(
        raw["ids"][0], raw["documents"][0], raw["metadatas"][0], raw["distances"][0]
    ):
        similarity = 1.0 - distance  # cosine distance -> similarity
        age = _age_days(meta["filed_date"], as_of)
        recency_weight = math.exp(-age / half_life_days)
        scored.append(
            {
                "id": doc_id,
                "text": text,
                "metadata": meta,
                "similarity": similarity,
                "age_days": age,
                "recency_weight": recency_weight,
                "score": similarity * recency_weight,
            }
        )

    scored.sort(key=lambda r: r["score"], reverse=True)
    return scored[:top_k]


def evict_before(collection, ticker: str, cutoff_date: str | dt.date) -> int:
    """Evict chunks filed before cutoff_date for a ticker - data expiry, so
    a stale filing can never be retrieved as the answer to a move it
    predates. Returns the number of chunks removed."""
    if isinstance(cutoff_date, dt.date):
        cutoff_date = cutoff_date.isoformat()
    existing = collection.get(where={"ticker": ticker})
    stale_ids = [
        doc_id
        for doc_id, meta in zip(existing["ids"], existing["metadatas"])
        if meta["filed_date"] < cutoff_date
    ]
    if stale_ids:
        collection.delete(ids=stale_ids)
    return len(stale_ids)
