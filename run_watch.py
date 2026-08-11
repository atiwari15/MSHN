"""The price loop: polls watchlist prices and fires trigger events.

When a move crosses the threshold this records the trigger and explains it
against whatever the corpus loop has already indexed. Explanations are
cached per (ticker, move_date), so a move that keeps breaching the
threshold all session is explained once, not once per poll - that cache is
what keeps a deployment's LLM spend bounded.

Usage:
    python run_watch.py                   # loop forever
    python run_watch.py --once            # single pass
    python run_watch.py --dry-run         # detect triggers, don't explain
"""

from __future__ import annotations

import argparse

from anthropic import Anthropic
from dotenv import load_dotenv

from config import PCT_CHANGE_THRESHOLD, WATCHLIST
from corpus.store import get_client, get_collection
from loop import add_loop_args, configure_logging, run_loop
from prices.client import FinnhubError, get_quote
from prices.trigger import check_quote
from rag.pipeline import explain_move

# Finnhub's free tier allows ~60 calls/min; one pass over a short watchlist
# every 60s is comfortably inside that.
DEFAULT_INTERVAL_SECONDS = 60

log = configure_logging("watch")


def watch_pass(conn, client, threshold: float, dry_run: bool) -> None:
    for ticker in WATCHLIST:
        try:
            quote = get_quote(ticker)
        except FinnhubError as exc:
            # One bad symbol or a rate-limit blip shouldn't stop the sweep.
            log.warning("%s: quote unavailable (%s)", ticker, exc)
            continue

        fired = check_quote(quote, threshold=threshold)
        if not fired:
            log.info("%s %+.2f%% (no trigger)", ticker, quote.pct_change * 100)
            continue

        log.info(
            "TRIGGER %s %s %.2f%% on %s",
            fired.ticker,
            fired.direction,
            abs(fired.pct_change) * 100,
            fired.move_date,
        )
        if dry_run:
            continue

        result = explain_move(
            conn,
            ticker=fired.ticker,
            move_date=fired.move_date,
            pct_change=fired.pct_change,
            baseline_close=fired.baseline_close,
            close=fired.close,
            client=client,
        )
        if result["cached"]:
            log.info("  already explained (trigger #%s), no LLM call", result["trigger_id"])
        else:
            log.info(
                "  explained (trigger #%s): catalyst_found=%s citations=%s tokens=%s/%s",
                result["trigger_id"],
                result["catalyst_found"],
                result["citations"],
                result.get("input_tokens"),
                result.get("output_tokens"),
            )


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    add_loop_args(parser, DEFAULT_INTERVAL_SECONDS)
    parser.add_argument("--threshold", type=float, default=PCT_CHANGE_THRESHOLD)
    parser.add_argument(
        "--dry-run", action="store_true", help="detect and log triggers without explaining them"
    )
    args = parser.parse_args()

    conn = get_collection(get_client())
    client = Anthropic()
    run_loop(
        log,
        lambda: watch_pass(conn, client, args.threshold, args.dry_run),
        interval=args.interval,
        once=args.once,
    )


if __name__ == "__main__":
    main()
