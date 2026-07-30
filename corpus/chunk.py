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
    fixture_id: str
    filed_date: str

    @property
    def id(self) -> str:
        return f"{self.fixture_id}:{self.doc_name}:{self.chunk_index}"

    def to_metadata(self) -> dict:
        return {
            "doc_name": self.doc_name,
            "doc_role": self.doc_role,
            "chunk_index": self.chunk_index,
            "fixture_id": self.fixture_id,
            "filed_date": self.filed_date,
            # Chroma's range filters ($lte etc.) require numeric operands,
            # so ISO dates get a parallel int form (YYYYMMDD) for filtering.
            "filed_date_int": int(self.filed_date.replace("-", "")),
        }


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


def load_fixture_chunks(fixture_dir: str | pathlib.Path) -> list[Chunk]:
    """Load a fixture folder's filing documents and split them into chunks."""
    fixture_dir = pathlib.Path(fixture_dir)
    meta = json.loads((fixture_dir / "metadata.json").read_text())
    fixture_id = meta["fixture_id"]
    filed_date = meta["filing"]["filed_date"]

    roles = _resolve_doc_roles(fixture_dir, meta)

    chunks: list[Chunk] = []
    for doc_name, role in roles.items():
        doc_path = fixture_dir / doc_name
        if not doc_path.exists():
            continue
        text = _html_to_text(doc_path.read_bytes())
        for i, piece in enumerate(_split_text(text)):
            chunks.append(
                Chunk(
                    text=piece,
                    doc_name=doc_name,
                    doc_role=role,
                    chunk_index=i,
                    fixture_id=fixture_id,
                    filed_date=filed_date,
                )
            )
    return chunks
