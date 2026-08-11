"""Ticker -> CIK resolution and filing fetch, with EDGAR rate limiting and User-Agent.

EDGAR's access rules are non-negotiable and are enforced here rather than at
each call site:

  - A descriptive User-Agent naming the app and a contact email is REQUIRED.
    Requests without one are rejected with HTTP 403.
  - The rate limit is 10 requests/second per IP, counted across ALL EDGAR
    domains together (www.sec.gov, data.sec.gov, efts.sec.gov). Every request
    in this module goes through one shared throttle so the budget can't be
    blown by using two endpoints concurrently.
  - Document filenames are never guessed; they are resolved from the
    accession folder's index.json.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import html
import json
import os
import pathlib
import re
import threading
import time

import requests

CACHE_DIR = pathlib.Path(__file__).resolve().parent.parent / ".cache" / "edgar"

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"

# Hard limit is 10 req/sec across all EDGAR domains; 8/sec leaves headroom
# for clock skew and in-flight overlap.
MAX_REQUESTS_PER_SECOND = 8.0
_MIN_INTERVAL = 1.0 / MAX_REQUESTS_PER_SECOND

# Caching cadences: the ticker map is regenerated daily, submissions change
# as filings land through the day, and filing documents never change once
# published (cached indefinitely).
TICKERS_TTL = dt.timedelta(hours=24)
SUBMISSIONS_TTL = dt.timedelta(hours=6)

MAX_RETRIES = 4


class EdgarError(RuntimeError):
    pass


@dataclasses.dataclass
class Filing:
    cik: str
    ticker: str
    form: str
    filed_date: str
    accession: str
    primary_document: str
    items: str = ""

    @property
    def accession_nodashes(self) -> str:
        return self.accession.replace("-", "")

    @property
    def archive_dir(self) -> str:
        return f"{ARCHIVES_BASE}/{int(self.cik)}/{self.accession_nodashes}"


class _Throttle:
    """Serializes outbound EDGAR requests to stay under the shared limit."""

    def __init__(self, min_interval: float) -> None:
        self._min_interval = min_interval
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last = time.monotonic()


_throttle = _Throttle(_MIN_INTERVAL)


def user_agent() -> str:
    ua = os.environ.get("EDGAR_USER_AGENT", "").strip()
    if not ua:
        raise EdgarError(
            "EDGAR_USER_AGENT is not set. EDGAR requires a descriptive User-Agent "
            "naming your app and a contact email, e.g. 'MSHN/0.1 (you@example.com)'. "
            "Requests without one are rejected with HTTP 403."
        )
    return ua


def get(url: str) -> bytes:
    """Rate-limited GET with backoff on 429/5xx."""
    headers = {"User-Agent": user_agent(), "Accept-Encoding": "gzip, deflate"}
    for attempt in range(MAX_RETRIES):
        _throttle.wait()
        response = requests.get(url, headers=headers, timeout=30)

        if response.status_code == 200:
            return response.content
        if response.status_code == 403:
            raise EdgarError(
                f"403 from EDGAR for {url}. This almost always means the User-Agent "
                f"was missing or rejected (currently: {user_agent()!r})."
            )
        # 429 = throttled, 5xx = transient. Back off exponentially and retry.
        if response.status_code == 429 or response.status_code >= 500:
            if attempt == MAX_RETRIES - 1:
                break
            time.sleep(2**attempt)
            continue
        raise EdgarError(f"HTTP {response.status_code} from EDGAR for {url}")

    raise EdgarError(f"EDGAR request failed after {MAX_RETRIES} attempts: {url}")


def _cached_get(url: str, cache_name: str, ttl: dt.timedelta | None) -> bytes:
    """Fetch with an on-disk cache. ttl=None means cache forever (filing
    documents are immutable once published)."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / cache_name

    if path.exists():
        if ttl is None:
            return path.read_bytes()
        age = dt.datetime.now() - dt.datetime.fromtimestamp(path.stat().st_mtime)
        if age < ttl:
            return path.read_bytes()

    blob = get(url)
    path.write_bytes(blob)
    return blob


def pad_cik(cik: str | int) -> str:
    """EDGAR returns unpadded CIKs but its APIs want 10 digits zero-padded."""
    return str(int(cik)).zfill(10)


def ticker_to_cik(ticker: str) -> str:
    """Resolve a ticker to its zero-padded 10-digit CIK. EDGAR indexes by
    CIK, not ticker, so this is step zero for everything else."""
    blob = _cached_get(TICKERS_URL, "company_tickers.json", TICKERS_TTL)
    table = json.loads(blob)
    target = ticker.upper()
    for row in table.values():
        if row["ticker"].upper() == target:
            return pad_cik(row["cik_str"])
    raise EdgarError(f"Ticker {ticker!r} not found in EDGAR's company_tickers.json")


def get_submissions(cik10: str) -> dict:
    url = SUBMISSIONS_URL.format(cik10=cik10)
    blob = _cached_get(url, f"submissions_{cik10}.json", SUBMISSIONS_TTL)
    return json.loads(blob)


def recent_filings(
    ticker: str,
    forms: list[str] | None = None,
    since: str | None = None,
    limit: int = 20,
) -> list[Filing]:
    """Most recent filings for a ticker, newest first, optionally filtered
    by form type and filing date.

    The submissions JSON stores filings as parallel arrays (one array of
    dates, one of accession numbers, and so on) rather than a list of
    objects, so they are zipped back into records here.
    """
    cik10 = ticker_to_cik(ticker)
    data = get_submissions(cik10)
    recent = data["filings"]["recent"]

    fields = ["form", "filingDate", "accessionNumber", "primaryDocument"]
    columns = [recent[f] for f in fields]
    items_col = recent.get("items", [""] * len(columns[0]))

    filings: list[Filing] = []
    for i, (form, filed, accession, primary) in enumerate(zip(*columns)):
        if forms and form not in forms:
            continue
        if since and filed < since:
            continue
        filings.append(
            Filing(
                cik=cik10,
                ticker=ticker.upper(),
                form=form,
                filed_date=filed,
                accession=accession,
                primary_document=primary,
                items=items_col[i] if i < len(items_col) else "",
            )
        )
        if len(filings) >= limit:
            break
    return filings


# The SGML header pairs each document's form type with its filename.
_SGML_DOC_RE = re.compile(r"<TYPE>([^<\n]+).*?<FILENAME>([^<\n]+)", re.S)


def filing_documents(filing: Filing) -> dict[str, str]:
    """Map filename -> document type (e.g. "8-K", "EX-99.1") for a filing.

    Read from the submission's SGML header rather than index.json: that
    endpoint's `type` field is the directory listing's *icon* name
    ("text.gif"), not the document type, so exhibits can't be told apart
    from the body there. Filenames are still never guessed - exhibit names
    vary per filer and per filing.
    """
    blob = _cached_get(
        f"{filing.archive_dir}/{filing.accession}-index-headers.html",
        f"headers_{filing.accession_nodashes}.html",
        None,
    )
    text = html.unescape(blob.decode("utf-8", errors="replace"))
    return {name.strip(): doc_type.strip() for doc_type, name in _SGML_DOC_RE.findall(text)}


def fetch_document(filing: Filing, doc_name: str) -> bytes:
    """Download one document from a filing. Cached forever - filing
    documents are immutable once published."""
    return _cached_get(
        f"{filing.archive_dir}/{doc_name}",
        f"doc_{filing.accession_nodashes}_{doc_name.replace('/', '_')}",
        None,
    )
