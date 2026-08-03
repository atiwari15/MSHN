"""Simple Streamlit UI: pick a fixture, see the price move, the retrieved
filing evidence, and the generated (or honestly withheld) explanation.

Run with: streamlit run streamlit_app.py
"""

from __future__ import annotations

import json
import pathlib

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from corpus.chunk import load_fixture_chunks
from corpus.store import add_chunks, get_client, get_collection
from rag.explain import explain
from rag.retrieve import TriggerEvent, retrieve

load_dotenv()

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"

st.set_page_config(page_title="MSHN", page_icon="📈", layout="wide")


@st.cache_resource
def _collection():
    return get_collection(get_client())


def _list_fixtures() -> list[str]:
    return sorted(p.name for p in FIXTURES_DIR.iterdir() if (p / "metadata.json").exists())


def _load_metadata(fixture_id: str) -> dict:
    return json.loads((FIXTURES_DIR / fixture_id / "metadata.json").read_text())


def _load_prices(fixture_id: str) -> pd.DataFrame | None:
    path = FIXTURES_DIR / fixture_id / "prices.json"
    if not path.exists():
        return None
    series = json.loads(path.read_text())["series"]
    df = pd.DataFrame(series)
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")


st.title("MSHN — Market Anomaly Explainer")
st.caption(
    "A price move triggers retrieval over recent SEC filings, and the system "
    "generates a grounded, cited explanation of what drove it."
)

fixture_ids = _list_fixtures()
if not fixture_ids:
    st.error("No fixtures found in fixtures/.")
    st.stop()

fixture_id = st.selectbox("Fixture", fixture_ids)
meta = _load_metadata(fixture_id)

trigger = TriggerEvent(
    ticker=meta["ticker"],
    as_of=meta["event"]["move_trading_day"],
    pct_change=meta["trigger"]["pct_change_close"],
)
direction = "fell" if trigger.pct_change < 0 else "rose"

st.subheader(f"{meta['company']} ({trigger.ticker})")
st.write(meta["description"])

col1, col2 = st.columns([2, 1])

with col1:
    prices = _load_prices(fixture_id)
    if prices is not None:
        st.line_chart(prices["close"])
    st.metric(f"Move on {trigger.as_of}", f"{direction} {abs(trigger.pct_change):.1%}")

with col2:
    st.write("**Filing**")
    st.write(f"Form {meta['filing']['form']}")
    st.write(meta["filing"]["item"])
    st.write(f"Filed {meta['filing']['filed_date']}")

if st.button("Retrieve & explain", type="primary"):
    with st.spinner("Indexing filing and retrieving..."):
        chunks = load_fixture_chunks(FIXTURES_DIR / fixture_id)
        collection = _collection()
        add_chunks(collection, chunks, ticker=trigger.ticker)
        results = retrieve(collection, trigger)

    st.write(f"**Retrieved evidence** ({len(results)} chunks)")
    for r in results:
        m = r["metadata"]
        label = f"{m['doc_role']} · chunk {m['chunk_index']} · score {r['score']:.3f}"
        with st.expander(label):
            st.write(r["text"])

    with st.spinner("Generating explanation..."):
        result = explain(trigger, results)

    st.write("**Explanation**")
    if result["catalyst_found"]:
        st.success(result["explanation"])
        st.caption("Citations: " + ", ".join(result["citations"]))
    else:
        st.warning(result["explanation"])

    gt = meta.get("ground_truth")
    if gt:
        st.write("**Ground truth (for comparison)**")
        badge = "✅ catalyst present" if gt["catalyst_present"] else "🚫 no catalyst"
        st.write(f"{badge} — {gt['one_line']}")
