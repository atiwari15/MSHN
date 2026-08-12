"use client";

import { useState } from "react";
import {
  streamExplain,
  type ExplainResult,
  type Fixture,
  type RetrievedChunk,
} from "@/lib/api";
import Sparkline from "./Sparkline";

function pct(v: number) {
  return `${v >= 0 ? "+" : ""}${(v * 100).toFixed(2)}%`;
}

export default function ExplainPanel({ fixture }: { fixture: Fixture }) {
  const [retrieved, setRetrieved] = useState<RetrievedChunk[] | null>(null);
  const [text, setText] = useState("");
  const [result, setResult] = useState<ExplainResult | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [openChunk, setOpenChunk] = useState<number | null>(null);

  const run = async (force: boolean) => {
    setRunning(true);
    setError(null);
    setRetrieved(null);
    setText("");
    setResult(null);
    try {
      await streamExplain(
        {
          ticker: fixture.ticker,
          move_date: fixture.move_date,
          pct_change: fixture.pct_change,
          force,
        },
        {
          onRetrieved: setRetrieved,
          onDelta: (d) => setText((prev) => prev + d),
          onResult: (r) => {
            setResult(r);
            if (r.explanation) setText(r.explanation);
          },
          onError: setError,
        },
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRunning(false);
    }
  };

  const correct =
    result !== null && result.catalyst_found === fixture.catalyst_present;

  return (
    <div className="rounded-lg border border-edge bg-panel p-4">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="font-semibold">
              {fixture.ticker}{" "}
              <span className="font-normal text-muted">{fixture.company}</span>
            </h3>
            <span
              className={`text-xs tabular-nums font-semibold ${
                fixture.pct_change >= 0 ? "text-up" : "text-down"
              }`}
            >
              {pct(fixture.pct_change)}
            </span>
            <span className="text-xs text-muted">on {fixture.move_date}</span>
          </div>
          <p className="text-sm text-muted mt-1 max-w-2xl">{fixture.description}</p>
        </div>
        <Sparkline points={fixture.prices} />
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button
          onClick={() => run(false)}
          disabled={running}
          className="rounded bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
          {running ? "Explaining…" : "Retrieve & explain"}
        </button>
        <button
          onClick={() => run(true)}
          disabled={running}
          className="rounded border border-edge px-3 py-1.5 text-sm disabled:opacity-50"
          title="Bypass the cached explanation and regenerate"
        >
          Force regenerate
        </button>
        <span className="text-xs text-muted">
          expected:{" "}
          {fixture.catalyst_present ? "a catalyst exists" : "no clear catalyst"}
        </span>
      </div>

      {error && <p className="mt-3 text-sm text-down">Error: {error}</p>}

      {retrieved && (
        <div className="mt-4">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-muted mb-2">
            Retrieved evidence ({retrieved.length})
          </h4>
          <ul className="space-y-1">
            {retrieved.map((c, i) => (
              <li key={i} className="rounded border border-edge">
                <button
                  onClick={() => setOpenChunk(openChunk === i ? null : i)}
                  className="flex w-full items-center gap-2 px-2 py-1.5 text-left text-xs"
                >
                  <span className="font-mono text-accent">{c.doc_role}</span>
                  <span className="text-muted truncate">{c.doc_name}</span>
                  <span className="ml-auto tabular-nums text-muted">
                    {c.score.toFixed(3)}
                  </span>
                </button>
                {openChunk === i && (
                  <p className="border-t border-edge px-2 py-2 text-xs whitespace-pre-wrap text-muted">
                    {c.text}
                  </p>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {(text || running) && (
        <div className="mt-4">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-muted mb-2">
            Explanation
          </h4>
          <p className="text-sm leading-relaxed whitespace-pre-wrap">
            {text}
            {running && <span className="animate-pulse">▍</span>}
          </p>
        </div>
      )}

      {result && (
        <div className="mt-4 border-t border-edge pt-3 text-xs space-y-1">
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            <span>
              catalyst found:{" "}
              <strong
                className={result.catalyst_found ? "text-up" : "text-down"}
              >
                {String(result.catalyst_found)}
              </strong>
            </span>
            <span className={correct ? "text-up" : "text-down"}>
              {correct ? "✓ matches ground truth" : "✗ disagrees with ground truth"}
            </span>
            {result.cached && <span className="text-muted">served from cache</span>}
            {result.input_tokens ? (
              <span className="text-muted">
                {result.input_tokens} in / {result.output_tokens} out tokens
              </span>
            ) : null}
          </div>
          {/* flex + gap rather than margins, so the roles stay separated for
              copy-paste and screen readers, not just visually */}
          {result.citations?.length > 0 && (
            <div className="flex flex-wrap items-center gap-2 text-muted">
              <span>citations:</span>
              {result.citations.map((c) => (
                <span key={c} className="font-mono text-accent">
                  {c}
                </span>
              ))}
            </div>
          )}
          <p className="text-muted">
            <strong>Ground truth:</strong> {fixture.ground_truth}
          </p>
        </div>
      )}
    </div>
  );
}
