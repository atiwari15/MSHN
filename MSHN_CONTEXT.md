# MSHN — Project Context & Handoff

> Hand this to a fresh assistant to get fully up to speed with no prior conversation. Covers what the project is, what's built, the non-obvious things learned the hard way, and what's left.

**Repo:** `github.com:atiwari15/MSHN.git` · **Local:** `/Users/aaravtiwari/MSHN` · **Branch:** `main`
**Last commit at time of writing:** `92d10b8` — Stage 4: Next.js frontend, Docker images, and deployment config
**Status:** ~90% complete. All four build stages done, live, and evaluated. Deployment itself not yet performed.

---

## 1. What MSHN is

A **live-RAG "market anomaly explainer."** It watches a small stock watchlist for unusual price moves, and when one fires, it retrieves relevant recent SEC filings and generates a grounded, cited explanation of the likely cause — or honestly reports **"no clear catalyst found"** when the evidence isn't there.

The honesty fallback is the differentiating feature. Naive explainers confabulate a reason for every move; MSHN declines when the filings don't support one.

**Why it can't be a single LLM call:** it needs two live streams working together — a *numeric* stream (prices) that acts as the **trigger**, and a *textual* stream (SEC filings) that acts as the **knowledge base**.

---

## 2. Architecture

Two loops on **separate clocks** — the load-bearing design decision.

```
price watcher ─┐
               ├─(trigger fires)→ recency-aware retrieval → grounded generation → explanation + citations
filing ingest ─┘        ↑                                        (or "no catalyst found")
                  Postgres + pgvector
                 (filings, chunks, triggers, cached explanations)
```

Ingestion runs **ahead of** the watcher deliberately: an 8-K often *is* the catalyst and lands right around the move it explains, so it must already be indexed when a trigger fires. Waiting until the price moves to start ingesting would be too late.

### File map

| Concern | Location |
|---|---|
| Watchlist, threshold, form types | `config.py` |
| EDGAR client (CIK resolution, rate limiting, doc fetch) | `edgar/client.py` |
| Filing ingestion into the store | `edgar/feed.py` |
| Finnhub quotes | `prices/client.py` |
| Percent-threshold trigger logic | `prices/trigger.py` |
| HTML → chunks | `corpus/chunk.py` |
| Local ONNX embeddings (384-dim) | `corpus/embed.py` |
| pgvector store: upsert, recency query, eviction | `corpus/store.py` |
| Trigger-driven retrieval | `rag/retrieve.py` |
| Generation + honesty fallback (+ streaming) | `rag/explain.py` |
| Shared trigger→retrieve→explain path w/ caching | `rag/pipeline.py` |
| Trigger & explanation persistence, usage totals | `state.py` |
| Shared loop scaffolding (signals, retry, `--once`) | `loop.py` |
| Corpus loop / price loop | `run_ingest.py` / `run_watch.py` |
| Offline fixture runner | `run_fixture.py` |
| HTTP API incl. SSE streaming | `app.py` |
| Frontend (Next.js 16.3, App Router, Tailwind v4) | `web/` |
| 4-axis eval harness | `eval/harness.py` |
| Labeled historical events | `fixtures/` |
| DB schema | `db/schema.sql` |
| Legacy Streamlit demo (superseded by `web/`) | `streamlit_app.py` |

---

## 3. Decisions locked in

| Decision | Choice | Notes |
|---|---|---|
| Watchlist | TSLA, NVDA, COIN, AAPL | Each exercises a different path — see below |
| Trigger | Simple percent threshold (±3%) vs prior close | Upgrade path: volatility-normalized, then price+volume |
| Filing forms | 8-K only | Later: Form 4, 10-Q/10-K, SC 13D/G |
| News | Out of scope | EDGAR-only keeps it clean and free |
| Price data | Finnhub free tier (~60 calls/min) | Verified working live |
| Text corpus | SEC EDGAR (free, no key) | |
| Vector store | **Postgres + pgvector** (migrated off Chroma) | One DB for vectors *and* app state |
| Embeddings | Local ONNX MiniLM, 384-dim | No embedding API key needed |
| LLM | Anthropic `claude-sonnet-5` | |
| Frontend | Next.js + FastAPI | |
| Hosting target | Railway or Render | **Not yet deployed** |
| Liveness | Live ingest + on-demand explain | Bounded LLM spend via caching |

**Watchlist roles:** TSLA/NVDA are workhorses (liquid, prolific 8-K filers). COIN is the trigger + honesty stress test (high beta — fired **5 triggers in one week** in real data, many with no company 8-K). AAPL is the calm control.

> **Not investment advice.** Tickers are chosen purely to generate good test data.

---

## 4. Hard-won gotchas — read this section

These cost real debugging time. Don't rediscover them.

### EDGAR
- **`index.json`'s `type` field is the directory *icon* name** (`"text.gif"`), NOT the document type. Exhibits cannot be distinguished from the filing body there. The authoritative source is the submission's **SGML header** (`{accession}-index-headers.html`), which pairs `<TYPE>EX-99.1` with `<FILENAME>`. Getting this wrong silently degrades every citation.
- User-Agent is **mandatory** (missing → HTTP 403). Rate limit is **10 req/s across all EDGAR domains combined**. Both enforced centrally in `edgar/client.py` via one shared throttle (measured at 8.3 req/s).
- CIKs come back unpadded; must be zero-padded to 10 digits for API URLs.
- Never guess document filenames — always resolve from the filing directory.

### Retrieval
- **Candidate selection must be recency-aware, not just similarity-aware.** Originally candidates were fetched top-N by similarity and *then* re-ranked by recency — but a re-rank can only reorder what it was given. A terse, recent 8-K ("we now need an export license") scores poorly against an earnings-shaped query, and 36 chunks of older earnings text crowded 3 of its 4 chunks out of the candidate set entirely. Now candidates are gathered on **both** axes (top-N by similarity ∪ top-N by recency) before re-ranking. This was a real false negative caught by a fixture.
- `doc_role` **collides across filings** — every 8-K has a `body_8k`. Anything identifying *which* filing a chunk came from must use `source_id`.
- A hard `filed_date <= as_of` cutoff prevents a filing from ever explaining a move it postdates.

### Anthropic API
- **`temperature` is deprecated for this model** — determinism can't be pinned that way.
- A response can spend its **entire `max_tokens` budget on extended thinking** and return with *no text block at all*. `content[0].text` will throw. Always find the first block with `type == "text"`, and keep `max_tokens` generous (currently 4000). This bit both generation and the eval judges.
- Judges asked for a count sometimes return a *list* instead; `eval/harness.py` accepts both.

### Eval methodology
- **Generation variance dominates judge variance.** Judging a *fixed* explanation is stable (±0.03 over 5 repeats); re-generating the explanation moves the score much more. Single-run scores swung 0.84 → 0.77 → 0.67 on identical retrieval. Always use `--runs 4 --judge-repeats 2` before drawing any conclusion.
- **Ground truth must not contain facts the filing can't supply.** The NVDA fixture originally listed "(beat consensus)" under `key_facts_from_filing`, but "consensus" appears **zero times** in either exhibit. That metric *penalized faithful behavior and would have rewarded confabulation.* Fixtures now split `key_facts_from_filing` (verifiably in the document) from `market_context` (true but external); only the former is scored.
- **Faithfulness and inference must be scored separately.** A filing never states *why* a stock moved, so scoring causal reasoning as an "unsupported claim" puts a permanent artificial ceiling on the metric. Splitting them revealed factual grounding was really 0.97 while the actual defect was inference discipline at 0.21.
- Retrieval recall is **only scored for catalyst-present fixtures** — when the indexed filing doesn't explain the move, retrieving it isn't success.

---

## 5. Evaluation

Four axes, scored separately, because one "accuracy" number hides the tradeoffs.

```bash
python -m eval.harness --runs 4 --judge-repeats 2
```

**Current scores (5 fixtures, `--runs 2 --judge-repeats 2`):**

| Metric | Score | Notes |
|---|---|---|
| Honesty accuracy | **100%** (3 TP, 2 TN) | Most trustworthy metric — binary, verifiable ground truth |
| Retrieval recall | **1.00** | Keyed on `source_id` |
| Faithfulness (facts grounded) | **0.98** | |
| Inference discipline | **0.75** | Was 0.21 before the prompt fix |
| Correctness | **0.63** | Least trustworthy — hand-written labels, LLM-judged, n=3 |

**Metric trustworthiness ranking:** honesty > faithfulness > retrieval > correctness. Correctness is simultaneously the weakest number and the least reliable one; don't lead with it.

**Known weak spot:** `coin_2026-02-13` scores correctness ~0.35, lowest in the set. Likely a retrieval-depth limit on a 1.4 MB shareholder letter where the filing-grounded positives (full-year growth, $11.3B cash, $1.7B buyback) sit far past headline figures that point the wrong way. Real finding, not noise (tight ±0.07 spread).

---

## 6. Fixtures

Labeled historical events. Filing `.htm` files are gitignored (reproducible via `python fixtures/fetch.py`).

| Fixture | Move | What it tests |
|---|---|---|
| `nvda_2025-02-26` | −8.5% | Trap case: record revenue/EPS, stock fell anyway (margin + guidance) |
| `nvda_2025-04-16` | −6.9% | Non-earnings catalyst — H20 export licence, ~$5.5B charges. Body-only 8-K, no exhibits |
| `coin_2026-02-13` | +16.5% | Up move; inverted trap — revenue *down* 5% Q/Q yet stock rallied |
| `coin_2026-06-05` | −7.1% | No catalyst, **no filing indexed** — empty-retrieval path (short-circuits, no LLM call) |
| `aapl_2025-05-12` | +6.2% | No catalyst, but a real same-day filing **IS** indexed (routine notes offering) — **the hard honesty case** |

The `aapl` fixture is the important one: `coin_2026-06-05` never actually tests the model (retrieval is empty so `explain()` short-circuits). `aapl_2025-05-12` puts real, recent, recency-favored documents in front of the model and requires it to judge them insufficient. **It passes** — returns "No clear catalyst found" with 11 output tokens.

Coverage: 3 catalyst-present / 2 absent · 2 up moves / 3 down · earnings and non-earnings catalysts.

---

## 7. Running it

Needs an Anthropic key, a Finnhub key (free tier), and a contact email for EDGAR's User-Agent. `.env` is gitignored and **already populated locally**.

```bash
docker compose up -d db          # Postgres + pgvector on port 5433
source .venv/bin/activate
```

```bash
python run_fixture.py nvda_2025-02-26      # offline replay, no live market needed
uvicorn app:app --reload --port 8000       # API
npm run dev --prefix web                    # frontend on :3000
python run_ingest.py                        # corpus loop (--once for single pass)
python run_watch.py --dry-run               # price loop, detect without explaining
docker compose up --build                   # full stack in containers
```

**API endpoints:** `/api/health`, `/api/watchlist`, `/api/triggers`, `/api/explain`, `/api/explain/stream` (SSE), `/api/fixtures`, `/api/fixtures/{id}/explain`, `/api/corpus`, `/api/usage`.

**Note:** Postgres is on **5433** (not 5432) to avoid colliding with any existing local install.

---

## 8. Production instincts already built in

Worth knowing about, and worth defending in an interview:

- **Incremental, idempotent indexing** — chunk IDs derive from the accession number, so re-ingesting a seen filing upserts rather than re-embedding the corpus. Verified: re-ingest leaves chunk count unchanged.
- **Explanation caching** per `(ticker, move_date)` — a move breaching the threshold on every poll all session is explained *once*. Verified: 6.3s → 0.00s, zero tokens on repeat. This is what bounds LLM spend under public traffic.
- **Cost metering** — token usage recorded per explanation, exposed at `/api/usage`.
- **Recency-vs-relevance ranking** — `similarity × exp(-age / half_life)`, half-life 21 days, tunable.
- **Data expiry** — `evict_before()` drops stale chunks per ticker.
- **Graceful shutdown** — loops finish the current pass on SIGINT/SIGTERM rather than dying mid-write. Verified with a real SIGTERM.
- **Per-pass error isolation** — a transient EDGAR 503 logs and retries next tick rather than killing an always-on service.
- **Citations reflect what was actually cited**, not everything retrieved (retrieval deliberately over-fetches).

---

## 9. What's left

1. **Actual deployment** — Dockerfiles, compose, and config are written but nothing is deployed. Needs the user's Railway/Render account, managed Postgres, and env vars. Creates billable resources, so it's the user's call.
2. **`coin_2026-02-13` correctness (~0.35)** — worth investigating retrieval depth on very large exhibits.
3. **More fixtures** — 5 is enough to have caught a real retrieval bug, not enough for precise numbers.
4. **Stretch (explicitly out of v1):** expose the explainer as an MCP server; add a news feed; volatility-normalized trigger; Form 4 / 10-Q / 10-K ingestion.

---

## 10. Working preferences

- **Git authorship: the user is the sole author.** No `Co-Authored-By: Claude` trailer on commits. Git identity is `atiwari15 <aaravtiwari15@gmail.com>`.
- The user prefers understanding *why* over just receiving code — explain design tradeoffs.
- Commit messages in this repo are prose-style, explaining reasoning and what was verified, not just what changed.
- The user asks good challenging questions about methodology; push back with evidence when a premise is off rather than agreeing reflexively.
- Assistant cannot run `git config` — the user must run identity/credential changes themselves.
