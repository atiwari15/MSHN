-- MSHN schema: filings corpus + retrieval vectors, and the app state that
-- the two loops produce (trigger events and their cached explanations).

CREATE EXTENSION IF NOT EXISTS vector;

-- One row per ingested filing document (8-K body or exhibit).
CREATE TABLE IF NOT EXISTS filings (
    id            BIGSERIAL PRIMARY KEY,
    ticker        TEXT        NOT NULL,
    cik           TEXT,
    accession     TEXT,
    form          TEXT,
    doc_name      TEXT        NOT NULL,
    doc_role      TEXT        NOT NULL,
    filed_date    DATE        NOT NULL,
    source_id     TEXT        NOT NULL,  -- fixture_id offline, accession live
    ingested_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_id, doc_name)
);

CREATE INDEX IF NOT EXISTS filings_ticker_filed_idx ON filings (ticker, filed_date DESC);

-- Chunks are what retrieval actually searches. 384 dims = all-MiniLM-L6-v2,
-- the local ONNX model, so no embedding API key is required.
CREATE TABLE IF NOT EXISTS chunks (
    id            BIGSERIAL PRIMARY KEY,
    filing_id     BIGINT      NOT NULL REFERENCES filings(id) ON DELETE CASCADE,
    chunk_key     TEXT        NOT NULL UNIQUE,  -- stable id => idempotent upsert
    chunk_index   INT         NOT NULL,
    ticker        TEXT        NOT NULL,
    filed_date    DATE        NOT NULL,
    doc_role      TEXT        NOT NULL,
    doc_name      TEXT        NOT NULL,
    text          TEXT        NOT NULL,
    embedding     VECTOR(384) NOT NULL
);

-- Retrieval always filters by ticker and a filed_date <= trigger cutoff, so
-- the filter columns are indexed alongside the vector index.
CREATE INDEX IF NOT EXISTS chunks_ticker_filed_idx ON chunks (ticker, filed_date);

-- HNSW with cosine distance, matching the Chroma configuration this replaces.
CREATE INDEX IF NOT EXISTS chunks_embedding_idx
    ON chunks USING hnsw (embedding vector_cosine_ops);

-- Price moves that crossed the threshold (produced by the watcher loop).
CREATE TABLE IF NOT EXISTS triggers (
    id            BIGSERIAL PRIMARY KEY,
    ticker        TEXT        NOT NULL,
    move_date     DATE        NOT NULL,
    pct_change    REAL        NOT NULL,
    baseline_close REAL,
    close         REAL,
    detected_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (ticker, move_date)
);

-- Generated explanations, cached per trigger so repeat views cost nothing.
CREATE TABLE IF NOT EXISTS explanations (
    id             BIGSERIAL PRIMARY KEY,
    trigger_id     BIGINT      NOT NULL REFERENCES triggers(id) ON DELETE CASCADE,
    catalyst_found BOOLEAN     NOT NULL,
    explanation    TEXT        NOT NULL,
    citations      JSONB       NOT NULL DEFAULT '[]'::jsonb,
    retrieved      JSONB       NOT NULL DEFAULT '[]'::jsonb,  -- chunk keys + scores
    model          TEXT,
    input_tokens   INT,
    output_tokens  INT,
    generated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (trigger_id)
);
