"""The trigger -> retrieve -> explain path, with caching.

Shared by the price-watcher loop and (later) the API layer so both take
exactly the same route: check the cache, and only spend an LLM call when
this move has not been explained before.
"""

from __future__ import annotations

import datetime as dt

from rag.explain import explain, explain_stream
from rag.retrieve import TriggerEvent, retrieve
from state import get_explanation, record_trigger, save_explanation


def explain_move(
    conn,
    ticker: str,
    move_date: str | dt.date,
    pct_change: float,
    baseline_close: float | None = None,
    close: float | None = None,
    client=None,
    force: bool = False,
) -> dict:
    """Explain a price move, reusing the cached explanation when present.

    Returns the explanation dict with `trigger_id` attached, and `cached`
    set when no LLM call was made.
    """
    if isinstance(move_date, dt.date):
        move_date = move_date.isoformat()

    trigger_id = record_trigger(conn, ticker, move_date, pct_change, baseline_close, close)

    if not force:
        cached = get_explanation(conn, trigger_id)
        if cached is not None:
            return {**cached, "trigger_id": trigger_id}

    trigger = TriggerEvent(ticker=ticker.upper(), as_of=move_date, pct_change=pct_change)
    retrieved = retrieve(conn, trigger)
    outcome = explain(trigger, retrieved, client=client)

    save_explanation(conn, trigger_id, outcome, retrieved)
    return {**outcome, "trigger_id": trigger_id, "cached": False}


def explain_move_stream(
    conn,
    ticker: str,
    move_date: str | dt.date,
    pct_change: float,
    baseline_close: float | None = None,
    close: float | None = None,
    client=None,
    force: bool = False,
):
    """Streaming twin of explain_move, for pushing generation to a client.

    Yields ("retrieved", list) once retrieval is done, then ("delta", str)
    while generating, then ("result", dict). A cache hit yields the stored
    result immediately with no deltas.
    """
    if isinstance(move_date, dt.date):
        move_date = move_date.isoformat()

    trigger_id = record_trigger(conn, ticker, move_date, pct_change, baseline_close, close)

    if not force:
        cached = get_explanation(conn, trigger_id)
        if cached is not None:
            yield "result", {**cached, "trigger_id": trigger_id}
            return

    trigger = TriggerEvent(ticker=ticker.upper(), as_of=move_date, pct_change=pct_change)
    retrieved = retrieve(conn, trigger)
    yield "retrieved", retrieved

    for kind, payload in explain_stream(trigger, retrieved, client=client):
        if kind == "delta":
            yield kind, payload
        else:
            save_explanation(conn, trigger_id, payload, retrieved)
            yield "result", {**payload, "trigger_id": trigger_id, "cached": False}
