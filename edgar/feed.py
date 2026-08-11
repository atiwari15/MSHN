"""Ingest recent filings for the watchlist into the vector store.

This is the corpus loop's body. It runs on its own clock, ahead of any
price trigger: an 8-K often *is* the catalyst and lands right around the
move, so waiting until a move fires to start ingesting would be too late.

Ingestion is incremental and idempotent - chunk ids are derived from the
accession number, so re-seeing a filing upserts rather than re-embedding
the corpus.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import re

from config import FORM_TYPES, WATCHLIST
from corpus.chunk import XBRL_FRAGMENT_RE, chunk_document
from corpus.store import add_chunks
from edgar.client import Filing, fetch_document, filing_documents, recent_filings

# How far back to look on a cold start. The recency weighting in retrieval
# already discounts older filings; this just bounds the initial backfill.
DEFAULT_LOOKBACK_DAYS = 30

# EX-99.* exhibits carry the substance of an earnings 8-K (press release,
# CFO commentary). The SGML header's document type is the authority.
_EXHIBIT_PREFIX = "EX-"

# XBRL taxonomy exhibits (EX-101.*, EX-104) are structured-data sidecars,
# not narrative text worth retrieving over.
_XBRL_EXHIBIT_RE = re.compile(r"^EX-10[14]", re.IGNORECASE)


@dataclasses.dataclass
class IngestResult:
    ticker: str
    accession: str
    filed_date: str
    documents: list[str]
    chunks_indexed: int


def _doc_role(doc_type: str, doc_name: str, primary_document: str) -> str:
    """Name the role a document plays, which is what citations refer to.

    e.g. "EX-99.1" -> "ex99_1", matching the doc_role vocabulary the
    fixtures use so offline and live citations read identically.
    """
    if doc_name == primary_document:
        return "body_8k"
    doc_type = doc_type.upper()
    if doc_type.startswith(_EXHIBIT_PREFIX):
        return "ex" + doc_type.removeprefix(_EXHIBIT_PREFIX).replace(".", "_").replace("-", "_").lower()
    return "other_document"


def _ingestable_documents(filing: Filing) -> dict[str, str]:
    """Filter a filing's directory down to narrative HTML documents,
    dropping auto-generated XBRL viewer fragments and non-HTML assets."""
    return {
        name: doc_type
        for name, doc_type in filing_documents(filing).items()
        if name.lower().endswith(".htm")
        and not name.lower().endswith("index.htm")
        and not XBRL_FRAGMENT_RE.match(name)
        and not _XBRL_EXHIBIT_RE.match(doc_type)
    }


def ingest_filing(conn, filing: Filing) -> IngestResult:
    """Download, chunk, and index every narrative document in one filing."""
    chunks = []
    documents = _ingestable_documents(filing)
    for doc_name, doc_type in documents.items():
        html = fetch_document(filing, doc_name)
        chunks.extend(
            chunk_document(
                html=html,
                doc_name=doc_name,
                doc_role=_doc_role(doc_type, doc_name, filing.primary_document),
                source_id=filing.accession,
                filed_date=filing.filed_date,
            )
        )

    add_chunks(conn, chunks, ticker=filing.ticker)
    return IngestResult(
        ticker=filing.ticker,
        accession=filing.accession,
        filed_date=filing.filed_date,
        documents=sorted(documents),
        chunks_indexed=len(chunks),
    )


def ingest_watchlist(
    conn,
    tickers: list[str] | None = None,
    forms: list[str] | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    limit_per_ticker: int = 5,
) -> list[IngestResult]:
    """Pull recent filings for each watchlist ticker into the store."""
    tickers = tickers or WATCHLIST
    forms = forms or FORM_TYPES
    since = (dt.date.today() - dt.timedelta(days=lookback_days)).isoformat()

    results = []
    for ticker in tickers:
        for filing in recent_filings(ticker, forms=forms, since=since, limit=limit_per_ticker):
            results.append(ingest_filing(conn, filing))
    return results
