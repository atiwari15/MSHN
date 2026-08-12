"use client";

import { useEffect, useState } from "react";
import { getTriggers, type Trigger } from "@/lib/api";

export default function TriggerFeed() {
  const [triggers, setTriggers] = useState<Trigger[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getTriggers()
      .then((d) => setTriggers(d.triggers))
      .catch((e) => setError((e as Error).message));
  }, []);

  return (
    <section>
      <h2 className="text-sm font-semibold uppercase tracking-wide text-muted mb-3">
        Trigger feed
      </h2>

      {error && <p className="text-sm text-down">Could not load: {error}</p>}
      {!error && triggers.length === 0 && (
        <p className="text-sm text-muted">
          No triggers recorded yet. The watcher records one when a move crosses
          the threshold.
        </p>
      )}

      <ul className="space-y-2">
        {triggers.map((t) => (
          <li key={t.id} className="rounded-lg border border-edge bg-panel p-3">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="font-semibold">{t.ticker}</span>
              <span
                className={`tabular-nums ${
                  t.pct_change >= 0 ? "text-up" : "text-down"
                }`}
              >
                {t.pct_change >= 0 ? "+" : ""}
                {(t.pct_change * 100).toFixed(2)}%
              </span>
              <span className="text-muted">{t.move_date}</span>
              {t.catalyst_found === null ? (
                <span className="text-xs text-muted">not yet explained</span>
              ) : t.catalyst_found ? (
                <span className="text-xs text-up">catalyst found</span>
              ) : (
                <span className="text-xs text-muted">no clear catalyst</span>
              )}
            </div>
            {t.explanation && (
              <p className="mt-2 text-sm leading-relaxed text-muted">
                {t.explanation}
              </p>
            )}
            {t.citations && t.citations.length > 0 && (
              <div className="mt-1 flex flex-wrap gap-2 text-xs">
                {t.citations.map((c) => (
                  <span key={c} className="font-mono text-accent">
                    {c}
                  </span>
                ))}
              </div>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
