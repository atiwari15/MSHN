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
the move. Explain the likely cause of the move using ONLY the retrieved excerpts.

If the excerpts don't plausibly explain the move (e.g. they're unrelated, too generic, or \
about something else entirely), respond with exactly this sentence and nothing else: \
"No clear catalyst found."

Your answer contains two kinds of statement, and they are held to different standards.

FACTS - what the filing says (figures, guidance, company statements). Every fact must \
appear in the excerpts and must carry a citation naming the document it came from, using \
the doc_role labels given with each excerpt, e.g. "(ex99_1)". Do not state a figure that \
is not in the excerpts.

INFERENCE - your reading of why the market reacted. The filing never states why a stock \
moved, so this is yours to draw, but it must be visibly built on the facts above and \
hedged honestly: "consistent with", "points to", "may reflect". Tie each inference to the \
specific fact that supports it rather than asserting it flatly.

You have no information beyond these excerpts. You do NOT know analyst estimates, \
consensus figures, whether results "beat" or "missed", what investors expected going in, \
how peer companies performed, or what happened in the wider market. Never assert any of \
these - not even when they seem obvious or you recall them from elsewhere. If the move \
looks larger than the filing alone accounts for, say so plainly instead of reaching for \
an outside explanation; an incomplete but honest answer is correct, an invented one is not.

Do not add meta-commentary about the retrieval itself (e.g. "no other catalysts appear in \
the excerpts").

Be concise: 3-6 sentences."""


def _format_context(chunks: list[dict]) -> str:
    blocks = []
    for c in chunks:
        meta = c["metadata"]
        blocks.append(
            f"[{meta['doc_role']} | filed {meta['filed_date']} | chunk {meta['chunk_index']}]\n{c['text']}"
        )
    return "\n\n---\n\n".join(blocks)


def _no_catalyst_result() -> dict:
    # No LLM call at all - the honest answer is free.
    return {
        "catalyst_found": False,
        "explanation": NO_CATALYST_MESSAGE,
        "citations": [],
        "model": None,
        "input_tokens": 0,
        "output_tokens": 0,
    }


def _build_prompt(trigger: TriggerEvent, chunks: list[dict]) -> str:
    direction = "fell" if trigger.pct_change < 0 else "rose"
    return (
        f"{trigger.ticker} stock {direction} {abs(trigger.pct_change):.1%} on {trigger.as_of}.\n\n"
        f"Retrieved filing excerpts:\n\n{_format_context(chunks)}"
    )


def _finalize(text: str, chunks: list[dict]) -> tuple[bool, list[str]]:
    """Citations are the roles the explanation actually cites, not every
    role that happened to be retrieved.

    Retrieval deliberately over-fetches, so the candidate set routinely
    includes documents the answer never uses - an unrelated older filing
    pulled in at a near-zero score would otherwise be reported as a source.
    """
    catalyst_found = NO_CATALYST_MESSAGE.lower() not in text.lower()
    if not catalyst_found:
        return False, []

    retrieved_roles = {c["metadata"]["doc_role"] for c in chunks}
    cited = sorted(role for role in retrieved_roles if role in text)
    # If the model cited nothing parseable, fall back to the roles that
    # actually carried weight rather than claiming all of them.
    if not cited:
        top_score = max((c["score"] for c in chunks), default=0.0)
        cited = sorted(
            {c["metadata"]["doc_role"] for c in chunks if c["score"] >= top_score / 2}
        )
    return True, cited


def explain_stream(trigger: TriggerEvent, chunks: list[dict], client: Anthropic | None = None):
    """Same contract as explain(), but yields text deltas as they arrive.

    Yields ("delta", str) while generating and finally ("result", dict) with
    the identical payload explain() returns, so the caller can persist it
    with the same code path.
    """
    if not chunks:
        yield "result", _no_catalyst_result()
        return

    client = client or Anthropic()
    parts: list[str] = []
    with client.messages.stream(
        model=DEFAULT_MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _build_prompt(trigger, chunks)}],
    ) as stream:
        for event in stream.text_stream:
            parts.append(event)
            yield "delta", event
        final = stream.get_final_message()

    text = "".join(parts).strip()
    catalyst_found, citations = _finalize(text, chunks)
    yield "result", {
        "catalyst_found": catalyst_found,
        "explanation": text,
        "citations": citations,
        "model": DEFAULT_MODEL,
        "input_tokens": final.usage.input_tokens,
        "output_tokens": final.usage.output_tokens,
    }


def explain(trigger: TriggerEvent, chunks: list[dict], client: Anthropic | None = None) -> dict:
    """Produce a grounded, cited explanation for a trigger event, or the
    honest 'no clear catalyst found' fallback when there's nothing to cite.
    """
    if not chunks:
        return _no_catalyst_result()

    client = client or Anthropic()
    response = client.messages.create(
        model=DEFAULT_MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _build_prompt(trigger, chunks)}],
    )
    # Responses may lead with non-text blocks (e.g. thinking), so take the
    # first block that actually carries text rather than assuming index 0.
    text = next(block.text for block in response.content if block.type == "text").strip()

    catalyst_found, citations = _finalize(text, chunks)
    return {
        "catalyst_found": catalyst_found,
        "explanation": text,
        "citations": citations,
        # Reported so the caller can persist per-explanation cost.
        "model": DEFAULT_MODEL,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
