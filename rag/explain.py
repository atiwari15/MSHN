"""Prompt + generation, with the 'no clear catalyst found' honesty fallback."""

from __future__ import annotations

import os

from anthropic import Anthropic

from rag.retrieve import TriggerEvent

DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
# Must cover extended thinking as well as the explanation itself; if the
# budget runs out mid-thinking the response carries no text block at all.
MAX_TOKENS = 4000

NO_CATALYST_MESSAGE = "No clear catalyst found."

SYSTEM_PROMPT = """You are MSHN, a market anomaly explainer. You are given a stock's price \
move and a set of retrieved SEC filing excerpts that were indexed before or on the day of \
the move. Explain the likely cause of the move using ONLY the retrieved excerpts - never \
speculate beyond what they say, and never invent a reason.

If the excerpts don't plausibly explain the move (e.g. they're unrelated, too generic, or \
about something else entirely), respond with exactly this sentence and nothing else: \
"No clear catalyst found."

When you do explain, cite which document each claim comes from (e.g. "(Exhibit 99.1 press \
release)" or "(Exhibit 99.2 CFO commentary)"), using the doc_role values given with each \
excerpt. Be concise: 3-6 sentences."""


def _format_context(chunks: list[dict]) -> str:
    blocks = []
    for c in chunks:
        meta = c["metadata"]
        blocks.append(
            f"[{meta['doc_role']} | filed {meta['filed_date']} | chunk {meta['chunk_index']}]\n{c['text']}"
        )
    return "\n\n---\n\n".join(blocks)


def explain(trigger: TriggerEvent, chunks: list[dict], client: Anthropic | None = None) -> dict:
    """Produce a grounded, cited explanation for a trigger event, or the
    honest 'no clear catalyst found' fallback when there's nothing to cite.
    """
    if not chunks:
        return {"catalyst_found": False, "explanation": NO_CATALYST_MESSAGE, "citations": []}

    client = client or Anthropic()
    direction = "fell" if trigger.pct_change < 0 else "rose"
    user_prompt = (
        f"{trigger.ticker} stock {direction} {abs(trigger.pct_change):.1%} on {trigger.as_of}.\n\n"
        f"Retrieved filing excerpts:\n\n{_format_context(chunks)}"
    )

    response = client.messages.create(
        model=DEFAULT_MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    # Responses may lead with non-text blocks (e.g. thinking), so take the
    # first block that actually carries text rather than assuming index 0.
    text = next(block.text for block in response.content if block.type == "text").strip()

    catalyst_found = NO_CATALYST_MESSAGE.lower() not in text.lower()
    citations = sorted({c["metadata"]["doc_role"] for c in chunks}) if catalyst_found else []

    return {"catalyst_found": catalyst_found, "explanation": text, "citations": citations}
