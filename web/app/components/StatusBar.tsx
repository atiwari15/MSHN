"use client";

import { useEffect, useState } from "react";
import { getCorpus, getHealth, getUsage } from "@/lib/api";

type Stats = {
  chunks: number;
  filings: number;
  explanations: number;
  inputTokens: number;
  outputTokens: number;
  status: string;
};

export default function StatusBar() {
  const [stats, setStats] = useState<Stats | null>(null);

  useEffect(() => {
    Promise.all([getHealth(), getCorpus(), getUsage()])
      .then(([health, corpus, usage]) =>
        setStats({
          chunks: health.corpus_chunks,
          filings: corpus.filings.length,
          explanations: usage.explanations,
          inputTokens: usage.input_tokens,
          outputTokens: usage.output_tokens,
          status: health.status,
        }),
      )
      .catch(() => setStats(null));
  }, []);

  if (!stats) {
    return (
      <p className="text-xs text-muted">
        API unreachable — start it with{" "}
        <code className="font-mono">uvicorn app:app --port 8000</code>
      </p>
    );
  }

  const cells = [
    ["corpus", `${stats.chunks.toLocaleString()} chunks`],
    ["filings indexed", String(stats.filings)],
    ["explanations generated", String(stats.explanations)],
    [
      "tokens",
      `${stats.inputTokens.toLocaleString()} in / ${stats.outputTokens.toLocaleString()} out`,
    ],
  ];

  return (
    <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs text-muted">
      <span>
        api:{" "}
        <strong className={stats.status === "ok" ? "text-up" : "text-down"}>
          {stats.status}
        </strong>
      </span>
      {cells.map(([label, value]) => (
        <span key={label}>
          {label}: <strong className="text-foreground">{value}</strong>
        </span>
      ))}
    </div>
  );
}
