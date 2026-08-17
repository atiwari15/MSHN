"""Index every fixture's filings into the corpus, ahead of any request.

The API indexes a fixture lazily, on the first call to
/api/fixtures/{id}/explain. That is fine locally, where embedding is fast,
but on a small deployed instance the largest fixture - Coinbase's 1.4 MB
Q4'25 shareholder letter - takes around half a minute to chunk and embed.
No browser waits that long, so the first click times out, retrieval runs
against a corpus that is still filling, and the demo appears to fail on
exactly the fixture that best demonstrates it.

Running this once after db.init moves that cost to provisioning time, where
nobody is watching. It embeds only; no LLM call is made and no explanation
is generated, so it is free.

Idempotent: chunk keys derive from the fixture id, so re-running upserts
rather than re-embedding. Safe to run against a populated database.

Usage:
    DATABASE_URL=postgresql://... python -m fixtures.seed
"""

from __future__ import annotations

import json
import pathlib
import sys
import time

from dotenv import load_dotenv

from corpus.chunk import load_fixture_chunks
from corpus.store import add_chunks, count, get_client

FIXTURES_DIR = pathlib.Path(__file__).parent


def fixture_ids() -> list[str]:
    return sorted(p.name for p in FIXTURES_DIR.iterdir() if (p / "metadata.json").exists())


def main() -> int:
    load_dotenv()

    conn = get_client()
    print(f"corpus before: {count(conn)} chunks")

    total = 0
    for fixture_id in fixture_ids():
        meta = json.loads((FIXTURES_DIR / fixture_id / "metadata.json").read_text())
        chunks = load_fixture_chunks(FIXTURES_DIR / fixture_id)

        if not chunks:
            # A fixture with no filing is the honesty-fallback stress test,
            # not a failure - it is meant to retrieve nothing.
            note = "no filing (by design)" if not meta.get("filing") else "no documents found"
            print(f"  {fixture_id:20} skipped - {note}")
            continue

        started = time.monotonic()
        add_chunks(conn, chunks, ticker=meta["ticker"])
        elapsed = time.monotonic() - started
        total += len(chunks)
        print(f"  {fixture_id:20} {len(chunks):4} chunks in {elapsed:5.1f}s")

    print(f"corpus after:  {count(conn)} chunks ({total} from fixtures this run)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
