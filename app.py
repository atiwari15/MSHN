"""FastAPI entry point.

Read paths (watchlist, trigger feed, corpus, usage) are cheap and always
live. The one expensive path - generating an explanation - goes through
rag.pipeline, which checks the cache first, so repeat views of the same
move cost nothing no matter how much traffic the deployment sees.

Run with:
    uvicorn app:app --reload --port 8000
"""

from __future__ import annotations

import json
import os
import pathlib

from anthropic import Anthropic
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from config import PCT_CHANGE_THRESHOLD, WATCHLIST
from corpus.chunk import load_fixture_chunks
from corpus.store import add_chunks, count, get_client, get_collection
from prices.client import FinnhubError, get_quote
from prices.trigger import check_quote
from rag.pipeline import explain_move, explain_move_stream
from state import recent_triggers, usage_totals

load_dotenv()

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"

# The Next.js dev server and whatever origin the deployed frontend uses.
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
    ).split(",")
    if o.strip()
]

app = FastAPI(title="MSHN", description="Market anomaly explainer", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

_conn = None


def db():
    """One long-lived connection; psycopg reconnects are cheap to recreate
    if the server drops it under us."""
    global _conn
    if _conn is None or _conn.closed:
        _conn = get_collection(get_client())
    return _conn


def anthropic_client() -> Anthropic:
    return Anthropic()


class ExplainRequest(BaseModel):
    ticker: str
    move_date: str = Field(description="ISO date of the move being explained")
    pct_change: float = Field(description="Signed fractional change, e.g. -0.085")
    force: bool = Field(default=False, description="Bypass the cached explanation")


@app.get("/api/health")
def health() -> dict:
    try:
        chunks = count(db())
        db_ok = True
    except Exception:
        chunks, db_ok = 0, False
    return {
        "status": "ok" if db_ok else "degraded",
        "database": db_ok,
        "corpus_chunks": chunks,
        "watchlist": WATCHLIST,
        "threshold": PCT_CHANGE_THRESHOLD,
    }


@app.get("/api/watchlist")
def watchlist() -> dict:
    """Live quotes for the watchlist, with whether each would fire the
    trigger right now."""
    out = []
    for ticker in WATCHLIST:
        try:
            quote = get_quote(ticker)
        except FinnhubError as exc:
            out.append({"ticker": ticker, "error": str(exc)})
            continue
        fired = check_quote(quote)
        out.append(
            {
                "ticker": ticker,
                "price": quote.current,
                "prior_close": quote.prior_close,
                "pct_change": quote.pct_change,
                "triggered": fired is not None,
                "as_of": quote.at.isoformat(),
            }
        )
    return {"threshold": PCT_CHANGE_THRESHOLD, "quotes": out}


@app.get("/api/triggers")
def triggers(limit: int = Query(default=20, le=100), ticker: str | None = None) -> dict:
    return {"triggers": recent_triggers(db(), limit=limit, ticker=ticker)}


@app.post("/api/explain")
def explain_endpoint(req: ExplainRequest) -> dict:
    return explain_move(
        db(),
        ticker=req.ticker,
        move_date=req.move_date,
        pct_change=req.pct_change,
        client=anthropic_client(),
        force=req.force,
    )


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@app.post("/api/explain/stream")
def explain_stream_endpoint(req: ExplainRequest) -> StreamingResponse:
    """Server-sent events: retrieval first, then generation token by token."""

    def events():
        try:
            for kind, payload in explain_move_stream(
                db(),
                ticker=req.ticker,
                move_date=req.move_date,
                pct_change=req.pct_change,
                client=anthropic_client(),
                force=req.force,
            ):
                if kind == "retrieved":
                    yield _sse(
                        "retrieved",
                        [
                            {
                                "doc_role": r["metadata"]["doc_role"],
                                "doc_name": r["metadata"]["doc_name"],
                                "filed_date": r["metadata"]["filed_date"],
                                "score": round(r["score"], 4),
                                "text": r["text"][:600],
                            }
                            for r in payload
                        ],
                    )
                else:
                    yield _sse(kind, payload)
        except Exception as exc:  # surface failures to the client, don't hang it
            yield _sse("error", {"message": str(exc)})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _fixture_ids() -> list[str]:
    return sorted(p.name for p in FIXTURES_DIR.iterdir() if (p / "metadata.json").exists())


def _fixture_meta(fixture_id: str) -> dict:
    path = FIXTURES_DIR / fixture_id / "metadata.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"unknown fixture {fixture_id}")
    return json.loads(path.read_text())


@app.get("/api/fixtures")
def fixtures() -> dict:
    out = []
    for fixture_id in _fixture_ids():
        meta = _fixture_meta(fixture_id)
        prices_path = FIXTURES_DIR / fixture_id / "prices.json"
        prices = json.loads(prices_path.read_text())["series"] if prices_path.exists() else []
        out.append(
            {
                "fixture_id": fixture_id,
                "ticker": meta["ticker"],
                "company": meta["company"],
                "description": meta["description"],
                "move_date": meta["event"]["move_trading_day"],
                "pct_change": meta["trigger"]["pct_change_close"],
                "catalyst_present": meta["ground_truth"]["catalyst_present"],
                "ground_truth": meta["ground_truth"]["one_line"],
                "has_filing": bool(meta.get("filing")),
                "prices": prices,
            }
        )
    return {"fixtures": out}


@app.post("/api/fixtures/{fixture_id}/explain")
def explain_fixture(fixture_id: str, force: bool = False) -> dict:
    """Index a fixture's filing and explain its move - the deterministic
    demo path, which needs no live market activity to show the pipeline."""
    meta = _fixture_meta(fixture_id)
    conn = db()

    chunks = load_fixture_chunks(FIXTURES_DIR / fixture_id)
    add_chunks(conn, chunks, ticker=meta["ticker"])

    result = explain_move(
        conn,
        ticker=meta["ticker"],
        move_date=meta["event"]["move_trading_day"],
        pct_change=meta["trigger"]["pct_change_close"],
        client=anthropic_client(),
        force=force,
    )
    return {
        "fixture_id": fixture_id,
        "chunks_indexed": len(chunks),
        "result": result,
        "ground_truth": {
            "catalyst_present": meta["ground_truth"]["catalyst_present"],
            "one_line": meta["ground_truth"]["one_line"],
        },
    }


@app.get("/api/corpus")
def corpus() -> dict:
    """What is currently indexed, newest filing first."""
    with db().cursor() as cur:
        cur.execute(
            """
            SELECT f.ticker, f.source_id, f.filed_date, count(c.id) AS chunks,
                   count(DISTINCT c.doc_name) AS documents
            FROM filings f LEFT JOIN chunks c ON c.filing_id = f.id
            GROUP BY f.ticker, f.source_id, f.filed_date
            ORDER BY f.filed_date DESC
            LIMIT 50
            """
        )
        rows = cur.fetchall()
    return {
        "total_chunks": count(db()),
        "filings": [
            {
                "ticker": r[0],
                "source_id": r[1],
                "filed_date": r[2].isoformat(),
                "chunks": r[3],
                "documents": r[4],
            }
            for r in rows
        ],
    }


@app.get("/api/usage")
def usage() -> dict:
    """Token spend across generated explanations - the cost-monitoring
    surface. Cached explanations cost nothing and never appear here twice."""
    return usage_totals(db())
