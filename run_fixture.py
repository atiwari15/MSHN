"""Stage 1 offline demo: load a fixture, index it, retrieve, and explain.

Usage:
    python run_fixture.py [fixture_id]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from dotenv import load_dotenv

from corpus.chunk import load_fixture_chunks
from corpus.store import add_chunks, count, get_client, get_collection
from rag.explain import explain
from rag.retrieve import TriggerEvent, retrieve

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"
DEFAULT_FIXTURE = "nvda_2025-02-26"


def run(fixture_id: str) -> None:
    fixture_dir = FIXTURES_DIR / fixture_id
    meta = json.loads((fixture_dir / "metadata.json").read_text())

    print(f"=== {meta['fixture_id']} ===")
    print(meta["description"])
    print()

    trigger = TriggerEvent(
        ticker=meta["ticker"],
        as_of=meta["event"]["move_trading_day"],
        pct_change=meta["trigger"]["pct_change_close"],
    )
    direction = "fell" if trigger.pct_change < 0 else "rose"
    print(f"Trigger: {trigger.ticker} {direction} {abs(trigger.pct_change):.1%} on {trigger.as_of}")
    print()

    print("Indexing filing documents...")
    chunks = load_fixture_chunks(fixture_dir)
    client = get_client()
    collection = get_collection(client)
    add_chunks(collection, chunks, ticker=trigger.ticker)
    print(f"  {len(chunks)} chunks indexed (corpus now has {count(collection)} total).")
    print()

    print("Retrieving...")
    results = retrieve(collection, trigger)
    for r in results:
        m = r["metadata"]
        print(
            f"  score={r['score']:.3f} sim={r['similarity']:.3f} "
            f"recency={r['recency_weight']:.2f}  {m['doc_role']} chunk#{m['chunk_index']}"
        )
    print()

    print("Explaining...")
    result = explain(trigger, results)
    print()
    print("--- Generated explanation ---")
    print(result["explanation"])
    print()
    print(f"Catalyst found: {result['catalyst_found']}")
    print(f"Citations: {', '.join(result['citations']) or 'none'}")

    gt = meta.get("ground_truth")
    if gt:
        print()
        print("--- Ground truth (for comparison) ---")
        print(f"Catalyst present: {gt['catalyst_present']}")
        print(gt["one_line"])


if __name__ == "__main__":
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture_id", nargs="?", default=DEFAULT_FIXTURE)
    args = parser.parse_args()

    if not (FIXTURES_DIR / args.fixture_id).exists():
        print(f"Unknown fixture: {args.fixture_id}", file=sys.stderr)
        sys.exit(1)

    run(args.fixture_id)
