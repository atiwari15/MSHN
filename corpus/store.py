"""Vector-store wrapper: incremental add, recency-aware query, expiry.

Backed by Postgres + pgvector. The interface mirrors what the Chroma
implementation exposed (get_collection / add_chunks / query / evict_before)
so retrieval and generation code is unaffected by the storage swap.
"""

from __future__ import annotations

import datetime as dt
import math
import os

import psycopg
from pgvector import Vector
from pgvector.psycopg import register_vector

from corpus.chunk import Chunk
from corpus.embed import embed_text, embed_texts

DEFAULT_DATABASE_URL = "postgresql://mshn:mshn@localhost:5433/mshn"

# How fast relevance decays with filing age, in the recency-aware re-rank.
# A chunk this many days old contributes half its raw similarity weight.
RECENCY_HALF_LIFE_DAYS = 21.0


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def get_client(url: str | None = None) -> psycopg.Connection:
    conn = psycopg.connect(url or database_url(), autocommit=True)
    register_vector(conn)
    return conn


def get_collection(conn: psycopg.Connection) -> psycopg.Connection:
    """Kept for interface parity with the previous Chroma-backed store -
    Postgres needs no per-collection handle, so the connection is the handle.
    """
    return conn


def add_chunks(conn: psycopg.Connection, chunks: list[Chunk], ticker: str) -> None:
    """Upsert by chunk_key, so re-ingesting an already-indexed filing is a
    no-op - the corpus loop should never need to re-embed the whole corpus
    just because it saw a filing again."""
    if not chunks:
        return

    vectors = [Vector(v) for v in embed_texts([c.text for c in chunks])]

    with conn.cursor() as cur:
        for chunk, vector in zip(chunks, vectors):
            cur.execute(
                """
                INSERT INTO filings (ticker, doc_name, doc_role, filed_date, source_id)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (source_id, doc_name) DO UPDATE SET ticker = EXCLUDED.ticker
                RETURNING id
                """,
                (ticker, chunk.doc_name, chunk.doc_role, chunk.filed_date, chunk.source_id),
            )
            filing_id = cur.fetchone()[0]

            cur.execute(
                """
                INSERT INTO chunks (
                    filing_id, chunk_key, chunk_index, ticker, filed_date,
                    doc_role, doc_name, text, embedding
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (chunk_key) DO UPDATE
                    SET text = EXCLUDED.text,
                        embedding = EXCLUDED.embedding,
                        doc_role = EXCLUDED.doc_role,
                        filed_date = EXCLUDED.filed_date
                """,
                (
                    filing_id,
                    chunk.id,
                    chunk.chunk_index,
                    ticker,
                    chunk.filed_date,
                    chunk.doc_role,
                    chunk.doc_name,
                    chunk.text,
                    vector,
                ),
            )


def count(conn: psycopg.Connection) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM chunks")
        return cur.fetchone()[0]


def query(
    conn: psycopg.Connection,
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

    # Wrapped so psycopg adapts it to pgvector's `vector` type; a bare list
    # would be sent as double precision[] and match no distance operator.
    query_vector = Vector(embed_text(query_text))

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT chunk_key, text, doc_role, doc_name, chunk_index,
                   filed_date, embedding <=> %s AS distance
            FROM chunks
            -- filed_date <= as_of: a filing that postdates the move being
            -- explained can never be the cited cause of it, even if it's
            -- semantically similar.
            WHERE ticker = %s AND filed_date <= %s
            ORDER BY embedding <=> %s
            LIMIT %s
            """,
            (query_vector, ticker, as_of, query_vector, overfetch),
        )
        rows = cur.fetchall()

    scored = []
    for chunk_key, text, doc_role, doc_name, chunk_index, filed_date, distance in rows:
        similarity = 1.0 - float(distance)  # cosine distance -> similarity
        age = max((as_of - filed_date).days, 0)
        recency_weight = math.exp(-age / half_life_days)
        scored.append(
            {
                "id": chunk_key,
                "text": text,
                "metadata": {
                    "doc_role": doc_role,
                    "doc_name": doc_name,
                    "chunk_index": chunk_index,
                    "filed_date": filed_date.isoformat(),
                    "ticker": ticker,
                },
                "similarity": similarity,
                "age_days": age,
                "recency_weight": recency_weight,
                "score": similarity * recency_weight,
            }
        )

    scored.sort(key=lambda r: r["score"], reverse=True)
    return scored[:top_k]


def evict_before(conn: psycopg.Connection, ticker: str, cutoff_date: str | dt.date) -> int:
    """Evict chunks filed before cutoff_date for a ticker - data expiry, so
    a stale filing can never be retrieved as the answer to a move it
    predates. Returns the number of chunks removed."""
    if isinstance(cutoff_date, str):
        cutoff_date = dt.date.fromisoformat(cutoff_date)
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM chunks WHERE ticker = %s AND filed_date < %s",
            (ticker, cutoff_date),
        )
        return cur.rowcount
