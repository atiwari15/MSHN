"""Finnhub quote client.

Finnhub's free tier covers real-time US equity quotes at roughly 60
calls/minute, which is ample for a handful of watchlist tickers polled on
a slow loop.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import os

import requests

QUOTE_URL = "https://finnhub.io/api/v1/quote"
DEFAULT_TIMEOUT = 15


class FinnhubError(RuntimeError):
    pass


@dataclasses.dataclass
class Quote:
    ticker: str
    current: float
    prior_close: float
    high: float
    low: float
    at: dt.datetime

    @property
    def pct_change(self) -> float:
        """Signed change against the prior close - the baseline the
        threshold trigger keys on."""
        if not self.prior_close:
            return 0.0
        return (self.current - self.prior_close) / self.prior_close


def api_key() -> str:
    key = os.environ.get("FINNHUB_API_KEY", "").strip()
    if not key:
        raise FinnhubError(
            "FINNHUB_API_KEY is not set. Get a free key at finnhub.io and add it to .env."
        )
    return key


def get_quote(ticker: str) -> Quote:
    response = requests.get(
        QUOTE_URL,
        params={"symbol": ticker.upper(), "token": api_key()},
        timeout=DEFAULT_TIMEOUT,
    )
    if response.status_code == 429:
        raise FinnhubError("Finnhub rate limit hit (free tier is ~60 calls/min).")
    if response.status_code != 200:
        raise FinnhubError(f"HTTP {response.status_code} from Finnhub for {ticker}")

    data = response.json()
    # Finnhub returns zeros rather than an error for unknown symbols.
    if not data.get("c"):
        raise FinnhubError(f"No quote data for {ticker!r} (unknown symbol?)")

    return Quote(
        ticker=ticker.upper(),
        current=data["c"],
        prior_close=data["pc"],
        high=data["h"],
        low=data["l"],
        at=dt.datetime.fromtimestamp(data["t"], tz=dt.timezone.utc) if data.get("t") else dt.datetime.now(dt.timezone.utc),
    )


def get_quotes(tickers: list[str]) -> dict[str, Quote]:
    return {ticker: get_quote(ticker) for ticker in tickers}
