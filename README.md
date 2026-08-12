# MSHN — Market Anomaly Explainer

Live-RAG over SEC filings. A price move triggers retrieval across recently-filed 8-Ks, and the system generates a grounded, cited explanation of what drove it — or reports **"no clear catalyst found"** when the evidence isn't there.

The honesty fallback is the point. Naive explainers invent a reason for every move; MSHN declines when the filings don't support one.

## Architecture

Two loops on **separate clocks**, which is the load-bearing design decision:

```
price watcher ─┐
               ├─(trigger fires)→ recency-aware retrieval → grounded generation → explanation + citations
filing ingest ─┘        ↑                                        (or "no catalyst found")
                  Postgres + pgvector
                 (filings, chunks, triggers, cached explanations)
```

Ingestion runs **ahead of** the watcher on purpose: an 8-K often *is* the catalyst and lands right around the move it explains, so it has to already be indexed when a trigger fires.

| Component | Location |
|---|---|
| EDGAR client (CIK resolution, rate limiting, document fetch) | `edgar/client.py` |
| Filing ingestion | `edgar/feed.py` |
| Finnhub quotes / threshold trigger | `prices/` |
| Chunking, embedding, vector store | `corpus/` |
| Retrieval + generation + honesty fallback | `rag/` |
| Trigger & explanation persistence | `state.py` |
| Always-on loops | `run_ingest.py`, `run_watch.py`, `loop.py` |
| HTTP API (incl. SSE streaming) | `app.py` |
| Frontend | `web/` |
| Eval harness | `eval/harness.py` |
| Labeled historical events | `fixtures/` |

## Running it

Requires an Anthropic key, a Finnhub key (free tier), and a contact email for EDGAR's required User-Agent.

```bash
cp .env.example .env   # then fill in your keys
docker compose up -d db
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Replay a labeled historical event offline — no live market activity needed:

```bash
python run_fixture.py nvda_2025-02-26
```

Run the API and the frontend:

```bash
uvicorn app:app --reload --port 8000
```

```bash
npm run dev --prefix web
```

Run the loops:

```bash
python run_ingest.py   # corpus loop; --once for a single pass
```

```bash
python run_watch.py    # price loop; --dry-run to detect without explaining
```

Full stack in containers:

```bash
docker compose up --build
```

## Evaluation

Four axes, scored separately, because a single "accuracy" number hides the tradeoffs:

- **Retrieval recall** — did it surface the right *filing*? (keyed on `source_id`, since `doc_role` collides across filings)
- **Faithfulness** — are the explanation's *factual* claims grounded in retrieved text?
- **Inference discipline** — does the *causal* reading follow from those facts, hedged honestly? A filing never states why a stock moved, so this is scored separately from faithfulness rather than penalized as unsupported.
- **Honesty** — a confusion matrix over catalyst-present vs. catalyst-found.

```bash
python -m eval.harness --runs 4 --judge-repeats 2
```

Both flags matter. Generation is non-deterministic and `temperature` is deprecated for the model, so single-run scores swing enough to be misleading — `--runs` repeats the whole pipeline, `--judge-repeats` repeats only the judge, and the harness reports the spread.

Ground truth in each fixture separates `key_facts_from_filing` (verifiably in the document) from `market_context` (true, but requiring analyst estimates or market commentary no SEC filing contains). Only the former is scored: grading against facts the filing cannot supply would penalize faithful behavior and reward confabulation.

## Fixtures

Labeled historical events, chosen to cover distinct failure modes:

| Fixture | Move | What it tests |
|---|---|---|
| `nvda_2025-02-26` | −8.5% | Trap case: record revenue, stock fell anyway |
| `nvda_2025-04-16` | −6.9% | Non-earnings catalyst (H20 export licence), body-only 8-K |
| `coin_2026-02-13` | +16.5% | Up move; inverted trap — revenue down Q/Q, stock rallied |
| `coin_2026-06-05` | −7.1% | No catalyst, no filing indexed (empty-retrieval path) |
| `aapl_2025-05-12` | +6.2% | No catalyst, but a real same-day filing **is** indexed — the hard honesty case |

Filing documents are gitignored because they're reproducible:

```bash
python fixtures/fetch.py
```

## Notes

- EDGAR requires a descriptive `User-Agent` (missing it returns 403) and limits to 10 req/s **across all its domains**; `edgar/client.py` enforces both centrally, plus TTL caching (24h tickers, 6h submissions, indefinite for immutable documents).
- Document types come from the submission's SGML header, not `index.json` — that endpoint's `type` field is the directory *icon* name, so exhibits can't be told apart from the filing body there.
- Explanations are cached per `(ticker, move_date)`, so repeat views cost nothing. `/api/usage` reports token spend.

**Not investment advice.** The watchlist is chosen to exercise the pipeline.
