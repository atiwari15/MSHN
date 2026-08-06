"""Scores retrieval quality, faithfulness, correctness, and the honesty check.

Runs every fixture in fixtures/ through the real chunk -> store -> retrieve ->
explain pipeline and scores the result against the ground truth each fixture
already carries in its metadata.json:

  - retrieval quality: did the expected filing sections show up in the
    retrieved set? (only meaningful for catalyst-present fixtures)
  - faithfulness: is every claim in the explanation supported by the
    retrieved context? (LLM judge; only run when a catalyst was found)
  - correctness: does the explanation cover the known key facts? (LLM
    judge; only run when a catalyst was found)
  - honesty: does catalyst_found match catalyst_present? (every fixture;
    aggregated into a confusion matrix across the fixture set)
"""

from __future__ import annotations

import json
import pathlib

from anthropic import Anthropic
from dotenv import load_dotenv

from corpus.chunk import load_fixture_chunks
from corpus.store import add_chunks, get_client, get_collection
from rag.explain import DEFAULT_MODEL, explain
from rag.retrieve import TriggerEvent, retrieve

FIXTURES_DIR = pathlib.Path(__file__).parent.parent / "fixtures"
RETRIEVAL_TOP_K = 6

FAITHFULNESS_JUDGE_SYSTEM = """You are a strict fact-checker. Given a generated explanation \
and the source excerpts it was supposed to be grounded in, break the explanation into its \
individual factual claims and determine whether each is directly supported by the excerpts.

Respond with ONLY a JSON object, no other text, in this exact shape:
{"total_claims": <int>, "supported_claims": <int>, "unsupported": ["<claim text>", ...]}"""

CORRECTNESS_JUDGE_SYSTEM = """You are checking a generated explanation against a list of known \
key facts about what actually happened. For each key fact, determine whether the explanation \
reflects it in substance (wording can differ).

Respond with ONLY a JSON object, no other text, in this exact shape:
{"total_facts": <int>, "covered_facts": <int>, "missing": ["<fact text>", ...]}"""


def _parse_json_response(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = "\n".join(text.split("\n")[1:])
        text = text.rsplit("```", 1)[0]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1:
        text = text[start : end + 1]
    return json.loads(text)


def _first_text(response) -> str:
    """Responses may lead with non-text blocks (e.g. thinking), so take the
    first block that actually carries text rather than assuming index 0."""
    return next(block.text for block in response.content if block.type == "text")


def _judge(client: Anthropic, system: str, user: str) -> dict:
    response = client.messages.create(
        model=DEFAULT_MODEL, max_tokens=1000, system=system, messages=[{"role": "user", "content": user}]
    )
    return _parse_json_response(_first_text(response))


def _score_faithfulness(client: Anthropic, explanation: str, chunks: list[dict]) -> dict | None:
    if not chunks:
        return None
    context = "\n\n---\n\n".join(c["text"] for c in chunks)
    result = _judge(client, FAITHFULNESS_JUDGE_SYSTEM, f"Explanation:\n{explanation}\n\nSource excerpts:\n{context}")
    total, supported = result["total_claims"], result["supported_claims"]
    result["score"] = supported / total if total else 1.0
    return result


def _score_correctness(client: Anthropic, explanation: str, key_facts: list[str]) -> dict | None:
    if not key_facts:
        return None
    facts_block = "\n".join(f"- {f}" for f in key_facts)
    result = _judge(client, CORRECTNESS_JUDGE_SYSTEM, f"Explanation:\n{explanation}\n\nKnown key facts:\n{facts_block}")
    total, covered = result["total_facts"], result["covered_facts"]
    result["score"] = covered / total if total else 1.0
    return result


def _score_retrieval(results: list[dict], meta: dict) -> dict | None:
    filing = meta.get("filing")
    if not filing:
        return None
    expected_roles = set(filing["documents"].keys())
    retrieved_roles = {r["metadata"]["doc_role"] for r in results}
    hit = expected_roles & retrieved_roles
    return {
        "expected_roles": sorted(expected_roles),
        "retrieved_roles": sorted(retrieved_roles),
        "recall": len(hit) / len(expected_roles) if expected_roles else 1.0,
    }


def list_fixture_ids() -> list[str]:
    return sorted(p.name for p in FIXTURES_DIR.iterdir() if (p / "metadata.json").exists())


def run_fixture(client: Anthropic, collection, fixture_id: str) -> dict:
    fixture_dir = FIXTURES_DIR / fixture_id
    meta = json.loads((fixture_dir / "metadata.json").read_text())

    trigger = TriggerEvent(
        ticker=meta["ticker"],
        as_of=meta["event"]["move_trading_day"],
        pct_change=meta["trigger"]["pct_change_close"],
    )

    chunks = load_fixture_chunks(fixture_dir)
    add_chunks(collection, chunks, ticker=trigger.ticker)
    results = retrieve(collection, trigger, top_k=RETRIEVAL_TOP_K)
    outcome = explain(trigger, results, client=client)

    gt = meta["ground_truth"]
    honesty_correct = outcome["catalyst_found"] == gt["catalyst_present"]

    faithfulness = correctness = None
    if outcome["catalyst_found"]:
        faithfulness = _score_faithfulness(client, outcome["explanation"], results)
        correctness = _score_correctness(client, outcome["explanation"], gt.get("key_facts_from_filing", []))

    return {
        "fixture_id": fixture_id,
        "ticker": trigger.ticker,
        "catalyst_present": gt["catalyst_present"],
        "catalyst_found": outcome["catalyst_found"],
        "honesty_correct": honesty_correct,
        "explanation": outcome["explanation"],
        "retrieval": _score_retrieval(results, meta),
        "faithfulness": faithfulness,
        "correctness": correctness,
    }


def summarize(results: list[dict]) -> dict:
    n = len(results)
    confusion = {"TP": 0, "TN": 0, "FP": 0, "FN": 0}
    for r in results:
        gt, found = r["catalyst_present"], r["catalyst_found"]
        key = "TP" if gt and found else "TN" if not gt and not found else "FP" if found else "FN"
        confusion[key] += 1

    def _avg(xs: list[float]) -> float | None:
        return sum(xs) / len(xs) if xs else None

    return {
        "n_fixtures": n,
        "honesty_accuracy": _avg([r["honesty_correct"] for r in results]),
        "confusion_matrix": confusion,
        "avg_retrieval_recall": _avg([r["retrieval"]["recall"] for r in results if r["retrieval"]]),
        "avg_faithfulness": _avg([r["faithfulness"]["score"] for r in results if r["faithfulness"]]),
        "avg_correctness": _avg([r["correctness"]["score"] for r in results if r["correctness"]]),
    }


def main() -> None:
    load_dotenv()
    client = Anthropic()
    collection = get_collection(get_client())

    results = [run_fixture(client, collection, fid) for fid in list_fixture_ids()]

    print("=== Per-fixture results ===")
    for r in results:
        print(f"\n{r['fixture_id']} ({r['ticker']})")
        print(
            f"  catalyst_present={r['catalyst_present']}  catalyst_found={r['catalyst_found']}"
            f"  honesty_correct={r['honesty_correct']}"
        )
        if r["retrieval"]:
            print(f"  retrieval recall: {r['retrieval']['recall']:.2f}  {r['retrieval']['retrieved_roles']}")
        if r["faithfulness"]:
            f = r["faithfulness"]
            print(f"  faithfulness: {f['score']:.2f}  ({f['supported_claims']}/{f['total_claims']} claims supported)")
        if r["correctness"]:
            c = r["correctness"]
            print(f"  correctness: {c['score']:.2f}  ({c['covered_facts']}/{c['total_facts']} key facts covered)")

    summary = summarize(results)
    print("\n=== Summary ===")
    print(f"Fixtures evaluated: {summary['n_fixtures']}")
    print(f"Honesty accuracy: {summary['honesty_accuracy']:.0%}")
    print(f"Confusion matrix: {summary['confusion_matrix']}")
    for label, key in [
        ("Avg retrieval recall", "avg_retrieval_recall"),
        ("Avg faithfulness", "avg_faithfulness"),
        ("Avg correctness", "avg_correctness"),
    ]:
        if summary[key] is not None:
            print(f"{label}: {summary[key]:.2f}")


if __name__ == "__main__":
    main()
