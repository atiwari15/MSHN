"""Persistence for what the two loops produce: fired triggers and the
explanations generated for them.

Separate from corpus/store.py, which owns the filings corpus and vector
retrieval. This module owns application state.

Explanations are cached per trigger. A trigger is unique on
(ticker, move_date), so the same move is explained once no matter how many
times it is viewed or how often the watcher re-sees it - which is what keeps
a public deployment's LLM spend bounded.
"""

from __future__ import annotations

import datetime as dt
import json


def record_trigger(
    conn,
    ticker: str,
    move_date: str | dt.date,
    pct_change: float,
    baseline_close: float | None = None,
    close: float | None = None,
) -> int:
    """Insert (or fetch) the trigger for a ticker/day and return its id."""
    if isinstance(move_date, dt.date):
        move_date = move_date.isoformat()

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO triggers (ticker, move_date, pct_change, baseline_close, close)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (ticker, move_date) DO UPDATE
                SET pct_change = EXCLUDED.pct_change,
                    baseline_close = COALESCE(EXCLUDED.baseline_close, triggers.baseline_close),
                    close = COALESCE(EXCLUDED.close, triggers.close)
            RETURNING id
            """,
            (ticker.upper(), move_date, pct_change, baseline_close, close),
        )
        return cur.fetchone()[0]


def get_explanation(conn, trigger_id: int) -> dict | None:
    """Return the cached explanation for a trigger, or None if not yet
    generated. Callers should check this before spending an LLM call."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT catalyst_found, explanation, citations, retrieved,
                   model, input_tokens, output_tokens, generated_at
            FROM explanations WHERE trigger_id = %s
            """,
            (trigger_id,),
        )
        row = cur.fetchone()

    if row is None:
        return None
    return {
        "catalyst_found": row[0],
        "explanation": row[1],
        "citations": row[2],
        "retrieved": row[3],
        "model": row[4],
        "input_tokens": row[5],
        "output_tokens": row[6],
        "generated_at": row[7],
        "cached": True,
    }


def save_explanation(conn, trigger_id: int, outcome: dict, retrieved: list[dict]) -> None:
    """Persist a generated explanation, keyed to its trigger."""
    # Store only what identifies the evidence, not the chunk text itself -
    # the text already lives in the chunks table.
    retrieved_ref = [
        {
            "chunk_key": r["id"],
            "doc_role": r["metadata"]["doc_role"],
            "doc_name": r["metadata"]["doc_name"],
            "filed_date": r["metadata"]["filed_date"],
            "score": round(r["score"], 4),
        }
        for r in retrieved
    ]

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO explanations (
                trigger_id, catalyst_found, explanation, citations, retrieved,
                model, input_tokens, output_tokens
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (trigger_id) DO UPDATE
                SET catalyst_found = EXCLUDED.catalyst_found,
                    explanation = EXCLUDED.explanation,
                    citations = EXCLUDED.citations,
                    retrieved = EXCLUDED.retrieved,
                    model = EXCLUDED.model,
                    input_tokens = EXCLUDED.input_tokens,
                    output_tokens = EXCLUDED.output_tokens,
                    generated_at = now()
            """,
            (
                trigger_id,
                outcome["catalyst_found"],
                outcome["explanation"],
                json.dumps(outcome["citations"]),
                json.dumps(retrieved_ref),
                outcome.get("model"),
                outcome.get("input_tokens"),
                outcome.get("output_tokens"),
            ),
        )


def recent_triggers(conn, limit: int = 20, ticker: str | None = None) -> list[dict]:
    """Trigger feed, newest first, with each trigger's explanation if one
    has been generated."""
    clause, params = ("WHERE t.ticker = %s", [ticker.upper()]) if ticker else ("", [])
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT t.id, t.ticker, t.move_date, t.pct_change, t.detected_at,
                   e.catalyst_found, e.explanation, e.citations
            FROM triggers t
            LEFT JOIN explanations e ON e.trigger_id = t.id
            {clause}
            ORDER BY t.move_date DESC, t.detected_at DESC
            LIMIT %s
            """,
            [*params, limit],
        )
        rows = cur.fetchall()

    return [
        {
            "id": r[0],
            "ticker": r[1],
            "move_date": r[2].isoformat(),
            "pct_change": r[3],
            "detected_at": r[4],
            "catalyst_found": r[5],
            "explanation": r[6],
            "citations": r[7],
        }
        for r in rows
    ]


def usage_totals(conn) -> dict:
    """Aggregate token spend across all generated explanations - the basis
    for the deployment's cost monitoring."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*), coalesce(sum(input_tokens), 0), coalesce(sum(output_tokens), 0)
            FROM explanations WHERE model IS NOT NULL
            """
        )
        count, input_tokens, output_tokens = cur.fetchone()
    return {
        "explanations": count,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
