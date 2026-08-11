"""Percent-threshold move detection against a rolling baseline.

Deliberately the simplest rule that works: fire when the move against the
prior close exceeds a fixed percentage. The upgrade path is documented
rather than built - volatility-normalized (std-devs of the stock's own
recent volatility) and then price+volume - because the threshold's job for
now is only to decide *when* to ask the retrieval question.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

from config import PCT_CHANGE_THRESHOLD
from prices.client import Quote


@dataclasses.dataclass
class TriggerFired:
    ticker: str
    move_date: str
    pct_change: float
    baseline_close: float
    close: float

    @property
    def direction(self) -> str:
        return "fell" if self.pct_change < 0 else "rose"


def check_threshold(
    ticker: str,
    close: float,
    baseline_close: float,
    move_date: str | dt.date | None = None,
    threshold: float = PCT_CHANGE_THRESHOLD,
) -> TriggerFired | None:
    """Return a TriggerFired when |move| crosses the threshold, else None.

    Kept independent of any price source so it can be driven identically by
    a live quote or a historical fixture series.
    """
    if not baseline_close:
        return None

    pct_change = (close - baseline_close) / baseline_close
    if abs(pct_change) < threshold:
        return None

    if move_date is None:
        move_date = dt.date.today()
    if isinstance(move_date, dt.date):
        move_date = move_date.isoformat()

    return TriggerFired(
        ticker=ticker.upper(),
        move_date=move_date,
        pct_change=pct_change,
        baseline_close=baseline_close,
        close=close,
    )


def check_quote(quote: Quote, threshold: float = PCT_CHANGE_THRESHOLD) -> TriggerFired | None:
    return check_threshold(
        ticker=quote.ticker,
        close=quote.current,
        baseline_close=quote.prior_close,
        move_date=quote.at.date(),
        threshold=threshold,
    )


def check_series(
    ticker: str,
    series: list[dict],
    threshold: float = PCT_CHANGE_THRESHOLD,
) -> list[TriggerFired]:
    """Run the threshold over a historical close series (fixture replay).

    Each entry needs `date` and `close`; the baseline is the prior entry's
    close, which is what the fixtures' `expected_trigger` encodes.
    """
    fired = []
    for prior, current in zip(series, series[1:]):
        hit = check_threshold(
            ticker=ticker,
            close=current["close"],
            baseline_close=prior["close"],
            move_date=current["date"],
            threshold=threshold,
        )
        if hit:
            fired.append(hit)
    return fired
