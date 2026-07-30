"""Recency-aware retrieval, triggered by a price move."""

from __future__ import annotations

import dataclasses

from corpus.store import query

DEFAULT_TOP_K = 6

# Trigger-driven queries look for the kind of content that explains a
# material move, mirroring what an 8-K Item 2.02 filing typically covers.
QUERY_TEMPLATE = (
    "{ticker} stock price {direction} {pct:.1%} on {date}. "
    "Results of operations, earnings, revenue, margins, guidance, "
    "material events explaining the change."
)


@dataclasses.dataclass
class TriggerEvent:
    ticker: str
    as_of: str  # ISO date the move happened
    pct_change: float  # signed, e.g. -0.085


def _direction(pct_change: float) -> str:
    return "fell" if pct_change < 0 else "rose"


def build_query_text(trigger: TriggerEvent) -> str:
    return QUERY_TEMPLATE.format(
        ticker=trigger.ticker,
        direction=_direction(trigger.pct_change),
        pct=abs(trigger.pct_change),
        date=trigger.as_of,
    )


def retrieve(collection, trigger: TriggerEvent, top_k: int = DEFAULT_TOP_K) -> list[dict]:
    """Retrieve the most relevant, recency-weighted chunks for a trigger."""
    query_text = build_query_text(trigger)
    return query(collection, query_text, ticker=trigger.ticker, as_of=trigger.as_of, top_k=top_k)
