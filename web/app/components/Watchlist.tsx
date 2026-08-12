"use client";

import { useEffect, useState } from "react";
import { getWatchlist, type Quote } from "@/lib/api";

const REFRESH_MS = 30_000;

function pct(v?: number) {
  if (v === undefined) return "—";
  return `${v >= 0 ? "+" : ""}${(v * 100).toFixed(2)}%`;
}

export default function Watchlist() {
  const [quotes, setQuotes] = useState<Quote[]>([]);
  const [threshold, setThreshold] = useState(0.03);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const data = await getWatchlist();
        if (cancelled) return;
        setQuotes(data.quotes);
        setThreshold(data.threshold);
        setError(null);
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      }
    };
    load();
    const id = setInterval(load, REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  return (
    <section>
      <div className="flex items-baseline justify-between mb-3">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-muted">
          Watchlist
        </h2>
        <span className="text-xs text-muted">
          trigger at ±{(threshold * 100).toFixed(0)}% · refreshes every 30s
        </span>
      </div>

      {error && (
        <p className="text-sm text-down mb-3">Could not load quotes: {error}</p>
      )}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {quotes.map((q) => (
          <div
            key={q.ticker}
            className={`rounded-lg border p-3 bg-panel ${
              q.triggered ? "border-accent" : "border-edge"
            }`}
          >
            <div className="flex items-center justify-between">
              <span className="font-semibold">{q.ticker}</span>
              {q.triggered && (
                <span className="text-[10px] uppercase tracking-wide font-semibold text-accent">
                  triggered
                </span>
              )}
            </div>
            {q.error ? (
              <p className="text-xs text-muted mt-2">{q.error}</p>
            ) : (
              <>
                <div className="text-xl font-semibold mt-1 tabular-nums">
                  ${q.price?.toFixed(2)}
                </div>
                <div
                  className={`text-sm tabular-nums ${
                    (q.pct_change ?? 0) >= 0 ? "text-up" : "text-down"
                  }`}
                >
                  {pct(q.pct_change)}
                </div>
                <div className="text-[11px] text-muted mt-1">
                  prior close ${q.prior_close?.toFixed(2)}
                </div>
              </>
            )}
          </div>
        ))}
        {quotes.length === 0 && !error && (
          <p className="text-sm text-muted">Loading quotes…</p>
        )}
      </div>
    </section>
  );
}
