"""Split filings into retrievable chunks.

Filing documents are always resolved by reading the accession/fixture
directory listing (and any unresolved metadata placeholders), never by
guessing filenames - the same rule the live EDGAR client must follow.
"""

from __future__ import annotations

import json
import pathlib
import re
import warnings
from dataclasses import dataclass

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

# SEC filings often embed inline XBRL, which trips bs4's "this looks like
# XML" heuristic even though we deliberately want HTML text extraction.
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# Auto-generated XBRL viewer fragments (financial statement tables), not
# narrative filing text - always skipped regardless of fixture/live source.
XBRL_FRAGMENT_RE = re.compile(r"^R\d+\.htm$", re.IGNORECASE)

CHUNK_SIZE_CHARS = 1500
CHUNK_OVERLAP_CHARS = 200


@dataclass
class Chunk:
    text: str
    doc_name: str
    doc_role: str
    chunk_index: int
    source_id: str  # fixture_id offline, accession number for live filings
    filed_date: str

    @property
    def id(self) -> str:
        """Stable across re-ingestion, so upserts are idempotent."""
        return f"{self.source_id}:{self.doc_name}:{self.chunk_index}"


def _resolve_doc_roles(fixture_dir: pathlib.Path, meta: dict) -> dict[str, str]:
    """Map filename -> role. Known filenames come from metadata; any
    remaining .htm file fills the first unresolved placeholder, and
    anything left over is treated as an additional exhibit."""
    docs = meta["filing"]["documents"]
    roles: dict[str, str] = {}
    known_names = {name for name in docs.values() if name and name != "DISCOVER_FROM_INDEX"}
    for name in known_names:
        roles[name] = next(role for role, n in docs.items() if n == name)

    for path in sorted(fixture_dir.glob("*.htm")):
        name = path.name
        if name in roles or XBRL_FRAGMENT_RE.match(name):
            continue
        placeholder_role = next(
            (role for role, n in docs.items() if n == "DISCOVER_FROM_INDEX"), None
        )
        if placeholder_role:
            roles[name] = placeholder_role
            docs[placeholder_role] = name
        else:
            roles[name] = "other_exhibit"

    return roles


def _html_to_text(html_bytes: bytes) -> str:
    soup = BeautifulSoup(html_bytes, "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()
    lines = (line.strip() for line in soup.get_text(separator="\n").splitlines())
    return "\n".join(line for line in lines if line)


def _split_text(text: str, size: int = CHUNK_SIZE_CHARS, overlap: int = CHUNK_OVERLAP_CHARS) -> list[str]:
    if not text:
        return []
    if len(text) <= size:
        return [text]
    pieces = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        pieces.append(text[start:end])
        if end == len(text):
            break
        start = end - overlap
    return pieces


def chunk_document(
    html: bytes,
    doc_name: str,
    doc_role: str,
    source_id: str,
    filed_date: str,
) -> list[Chunk]:
    """Split one filing document's HTML into chunks.

    Shared by the offline fixture loader and the live ingestion path, so
    both produce identically-shaped chunks from identical parsing rules.
    """
    text = _html_to_text(html)
    return [
        Chunk(
            text=piece,
            doc_name=doc_name,
            doc_role=doc_role,
            chunk_index=i,
            source_id=source_id,
            filed_date=filed_date,
        )
        for i, piece in enumerate(_split_text(text))
    ]


def load_fixture_chunks(fixture_dir: str | pathlib.Path) -> list[Chunk]:
    """Load a fixture folder's filing documents and split them into chunks.

    A fixture with `"filing": null` genuinely has no explanatory filing
    (the honesty-fallback stress test) and yields no chunks - this is
    distinct from a filing whose exhibit filename hasn't been resolved yet.
    """
    fixture_dir = pathlib.Path(fixture_dir)
    meta = json.loads((fixture_dir / "metadata.json").read_text())
    if not meta.get("filing"):
        return []
    fixture_id = meta["fixture_id"]
    filed_date = meta["filing"]["filed_date"]

    roles = _resolve_doc_roles(fixture_dir, meta)

    chunks: list[Chunk] = []
    for doc_name, role in roles.items():
        doc_path = fixture_dir / doc_name
        if not doc_path.exists():
            continue
        chunks.extend(
            chunk_document(doc_path.read_bytes(), doc_name, role, fixture_id, filed_date)
        )
    return chunks
