"""The corpus loop: continuously pulls fresh filings into the vector store.

Runs on its own clock, deliberately ahead of the price watcher. An 8-K
often *is* the catalyst and lands right around the move it explains, so a
filing has to already be indexed by the time a trigger fires - waiting
until the price moves to start ingesting would be too late.

Usage:
    python run_ingest.py                  # loop forever
    python run_ingest.py --once           # single pass
    python run_ingest.py --interval 600   # custom cadence
"""

from __future__ import annotations

import argparse

from dotenv import load_dotenv

from config import WATCHLIST
from corpus.store import count, get_client, get_collection
from edgar.feed import ingest_watchlist
from loop import add_loop_args, configure_logging, run_loop

# EDGAR publishes throughout the day; every 15 minutes keeps the corpus
# fresh without hammering a free public service.
DEFAULT_INTERVAL_SECONDS = 900
DEFAULT_LOOKBACK_DAYS = 30

log = configure_logging("ingest")


def ingest_pass(conn, lookback_days: int, limit_per_ticker: int) -> None:
    results = ingest_watchlist(
        conn, lookback_days=lookback_days, limit_per_ticker=limit_per_ticker
    )
    # Ingestion is idempotent, so a steady state of "0 new chunks" is the
    # expected quiet-period outcome, not a failure.
    new_chunks = sum(r.chunks_indexed for r in results)
    log.info(
        "pass complete: %d filings seen across %s, %d chunks written, corpus=%d",
        len(results),
        ",".join(WATCHLIST),
        new_chunks,
        count(conn),
    )
    for r in results:
        if r.chunks_indexed:
            log.info("  %s %s filed %s -> %d chunks", r.ticker, r.accession, r.filed_date, r.chunks_indexed)


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    add_loop_args(parser, DEFAULT_INTERVAL_SECONDS)
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--limit-per-ticker", type=int, default=5)
    args = parser.parse_args()

    conn = get_collection(get_client())
    run_loop(
        log,
        lambda: ingest_pass(conn, args.lookback_days, args.limit_per_ticker),
        interval=args.interval,
        once=args.once,
    )


if __name__ == "__main__":
    main()
