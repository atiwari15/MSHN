"use client";

import { useEffect, useState } from "react";
import ExplainPanel from "./components/ExplainPanel";
import StatusBar from "./components/StatusBar";
import TriggerFeed from "./components/TriggerFeed";
import Watchlist from "./components/Watchlist";
import { getFixtures, type Fixture } from "@/lib/api";

export default function Home() {
  const [fixtures, setFixtures] = useState<Fixture[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getFixtures()
      .then((d) => {
        setFixtures(d.fixtures);
        setSelected(d.fixtures[0]?.fixture_id ?? null);
      })
      .catch((e) => setError((e as Error).message));
  }, []);

  const active = fixtures.find((f) => f.fixture_id === selected);

  return (
    <main className="mx-auto w-full max-w-5xl px-5 py-8 space-y-10">
      <header className="space-y-3">
        <h1 className="text-2xl font-semibold tracking-tight">
          MSHN — Market Anomaly Explainer
        </h1>
        <p className="text-sm text-muted max-w-2xl">
          A price move triggers retrieval over recent SEC filings, and the system
          generates a grounded, cited explanation of what drove it — or reports{" "}
          <em>no clear catalyst found</em> when the evidence isn&apos;t there.
        </p>
        <StatusBar />
      </header>

      <Watchlist />

      <section>
        <div className="flex items-baseline justify-between mb-3">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-muted">
            Replay a labeled event
          </h2>
          <span className="text-xs text-muted">
            deterministic demo · no live move required
          </span>
        </div>

        {error && (
          <p className="text-sm text-down">Could not load fixtures: {error}</p>
        )}

        <div className="flex flex-wrap gap-2 mb-4">
          {fixtures.map((f) => (
            <button
              key={f.fixture_id}
              onClick={() => setSelected(f.fixture_id)}
              className={`rounded border px-2.5 py-1 text-xs font-mono ${
                selected === f.fixture_id
                  ? "border-accent text-accent"
                  : "border-edge text-muted"
              }`}
            >
              {f.fixture_id}
              {!f.catalyst_present && " ·  no catalyst"}
            </button>
          ))}
        </div>

        {active && <ExplainPanel key={active.fixture_id} fixture={active} />}
      </section>

      <TriggerFeed />

      <footer className="border-t border-edge pt-4 text-xs text-muted">
        Not investment advice. Tickers are chosen to exercise the pipeline.
      </footer>
    </main>
  );
}
