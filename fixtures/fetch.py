"""Populate a fixture folder with its filing documents, straight from EDGAR.

Fixture .htm files are gitignored because they are reproducible: this script
rebuilds any of them from the accession recorded in metadata.json, resolving
document filenames from the filing itself rather than trusting the metadata
(the same rule the live client follows).

Usage:
    python fixtures/fetch.py                # every fixture that has a filing
    python fixtures/fetch.py nvda_2025-04-16
"""

from __future__ import annotations

import json
import pathlib
import sys

from dotenv import load_dotenv

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from corpus.chunk import XBRL_FRAGMENT_RE  # noqa: E402
from edgar.client import Filing, fetch_document, filing_documents  # noqa: E402

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent


def fetch_fixture(fixture_id: str) -> None:
    fixture_dir = FIXTURES_DIR / fixture_id
    meta = json.loads((fixture_dir / "metadata.json").read_text())
    filing_meta = meta.get("filing")
    if not filing_meta:
        print(f"{fixture_id}: no filing (catalyst-absent fixture), nothing to fetch")
        return

    filing = Filing(
        cik=meta["cik_padded"],
        ticker=meta["ticker"],
        form=filing_meta["form"],
        filed_date=filing_meta["filed_date"],
        accession=filing_meta["accession"],
        primary_document=filing_meta["documents"]["body_8k"],
    )

    wanted = {
        name: doc_type
        for name, doc_type in filing_documents(filing).items()
        if name.lower().endswith(".htm")
        and not name.lower().endswith("index.htm")
        and not XBRL_FRAGMENT_RE.match(name)
        and not doc_type.upper().startswith("EX-10")
    }

    print(f"{fixture_id}: {len(wanted)} document(s)")
    for name, doc_type in wanted.items():
        blob = fetch_document(filing, name)
        (fixture_dir / name).write_bytes(blob)
        print(f"  saved {name} ({doc_type}, {len(blob):,} bytes)")


def main() -> None:
    load_dotenv()
    if len(sys.argv) > 1:
        targets = sys.argv[1:]
    else:
        targets = sorted(
            p.name for p in FIXTURES_DIR.iterdir() if (p / "metadata.json").exists()
        )
    for fixture_id in targets:
        fetch_fixture(fixture_id)


if __name__ == "__main__":
    main()
