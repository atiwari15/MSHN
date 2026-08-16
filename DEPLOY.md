# Deploying MSHN

Split deployment: the Next.js frontend goes to Vercel, everything Python
goes to Railway.

## Why split

Vercel cannot host the Python side, for three independent reasons:

1. **The embedding model does not fit.** `corpus/embed.py` loads a local
   ONNX MiniLM through Chroma. onnxruntime (74 MB) + chromadb_rust_bindings
   (52 MB) + numpy (40 MB) + the model itself (~90 MB) blow past Vercel's
   250 MB uncompressed function limit before FastAPI, anthropic, lxml and
   psycopg are counted. `Dockerfile` bakes the model into the image so cold
   starts do not pay for it; serverless has no equivalent.
2. **The loops are always-on.** `run_ingest.py` and `run_watch.py` are
   long-lived processes on separate clocks. Vercel has no such runtime.
   (They *would* map onto Cron via the existing `--once` flag - see
   `loop.py` - but only after problem 1 is solved.)
3. **No Postgres.** pgvector has to come from somewhere else regardless.

Moving to a hosted embedding API would fix (1) and (2), at the cost of a
`VECTOR(384)` -> larger-dim schema migration, re-embedding the whole corpus,
and giving up the "no embedding API key required" property. Not worth it.

## Topology

```
Vercel                    Railway
------                    -------
web/  (Next.js)  ──HTTP──▶ api     (uvicorn app:app)
                           ingest  (python run_ingest.py)   ─┐
                           watch   (python run_watch.py)    ─┼─▶ postgres
                                                             ─┘   (pgvector)
```

All three Railway services build from the same root `Dockerfile` and differ
only by start command - the same arrangement as `docker-compose.yml`, which
is the local rehearsal of this exact topology.

## Order of operations

There is a circular dependency to break: Vercel bakes `NEXT_PUBLIC_API_BASE`
into the build (`web/lib/api.ts`), so it needs the API's URL first - but the
API needs Vercel's domain in `CORS_ORIGINS`. Resolve it by doing Railway
first and setting CORS last:

### 1. Railway - database

- New project, add a **Postgres** service.
- Copy its `DATABASE_URL`.

pgvector must be available. You do not need to check separately: step 2's
schema apply begins with `CREATE EXTENSION IF NOT EXISTS vector` and will
fail loudly if it is missing. If it does fail, provision the database on
**Neon** instead (pgvector is standard there) and use its connection string.

### 2. Apply the schema - once, from your machine

Managed Postgres does not run `db/schema.sql` for you; only the compose
setup does, via `docker-entrypoint-initdb.d`. Without this the tables do
not exist and every request fails.

```bash
DATABASE_URL='<railway-postgres-url>' python -m db.init
```

Expect: `ok - tables present: chunks, explanations, filings, triggers`.

Run this *before* starting the services, not from service startup - the API
and both loops boot concurrently and racing `CREATE TABLE IF NOT EXISTS`
against each other can deadlock.

### 3. Railway - three services from this repo

Each one: same repo, root `Dockerfile`, differing start command.

| Service  | Start command                        | Notes |
|----------|--------------------------------------|-------|
| `api`    | `uvicorn app:app --host 0.0.0.0 --port $PORT` | Generate a public domain for this one |
| `ingest` | `python run_ingest.py`               | No public domain |
| `watch`  | `python run_watch.py`                | No public domain |

Only `api` gets a public domain; the loops are workers and should not be
reachable.

Environment variables - all three services need all of these:

| Variable | Value |
|---|---|
| `DATABASE_URL` | Railway's Postgres reference variable |
| `ANTHROPIC_API_KEY` | from `.env` |
| `FINNHUB_API_KEY` | from `.env` |
| `EDGAR_USER_AGENT` | from `.env` - mandatory, EDGAR 403s without it |
| `CORS_ORIGINS` | set in step 5 (`api` only) |

Let `ingest` run one pass before doing anything else, so the corpus is not
empty when the first trigger fires. That ordering is the whole point of the
two-clock design.

### 4. Vercel - frontend

- New project from this repo, **Root Directory: `web`**. Vercel
  auto-detects Next.js; `output: "standalone"` in `next.config.ts` is inert
  there and harmless.
- Environment variable: `NEXT_PUBLIC_API_BASE` = the Railway `api` domain
  (`https://...up.railway.app`, no trailing slash).
- Deploy.

`NEXT_PUBLIC_API_BASE` is inlined at build time. Changing it later requires
a redeploy, not just an env var edit.

### 5. Close the loop - CORS

Back on Railway, set on `api`:

```
CORS_ORIGINS=https://<your-project>.vercel.app
```

Redeploy `api`.

Preview deploys get randomised hostnames that no exact-match list can
cover. If you want them working, also set:

```
CORS_ORIGIN_REGEX=^https://<your-project>-[a-z0-9-]+\.vercel\.app$
```

Keep it anchored (`^...$`) and specific to your project. A loose pattern
here is a real security hole, which is why it is opt-in and unset by
default.

## Verify

```bash
curl https://<api-domain>/api/health      # status, corpus_chunks, watchlist
curl https://<api-domain>/api/corpus      # what ingest has indexed so far
curl https://<api-domain>/api/usage       # token spend, should start at zero
```

Then in the browser, on the Vercel URL:

- Watchlist renders live quotes - proves Finnhub and CORS.
- Run a fixture, e.g. `nvda_2025-04-16` - proves retrieval, generation and
  SSE streaming end to end. The **first** call to a fixture is slow: it
  embeds the filing on demand (`app.py:228`), and `coin_2026-02-13` is a
  1.4 MB document. Repeat calls hit the explanation cache and return in
  milliseconds with zero tokens.
- Run `aapl_2025-05-12` - the honesty case. It should return "no clear
  catalyst found" despite having a real same-day filing indexed.

## Cost control

The expensive path is bounded by design and worth confirming in production:

- Explanations are cached per `(ticker, move_date)`, so a move that breaches
  the threshold on every poll all session is generated once.
- `/api/usage` reports cumulative token spend.
- `run_watch.py --dry-run` detects triggers without generating, if you want
  the watcher live but silent.

## Not covered

- The eval harness is a local tool; it is not deployed and does not need to be.
- `streamlit_app.py` is superseded by `web/`. Its dependencies live in
  `requirements-dev.txt` and are deliberately absent from the image.
