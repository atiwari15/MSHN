export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export type Quote = {
  ticker: string;
  price?: number;
  prior_close?: number;
  pct_change?: number;
  triggered?: boolean;
  as_of?: string;
  error?: string;
};

export type Trigger = {
  id: number;
  ticker: string;
  move_date: string;
  pct_change: number;
  catalyst_found: boolean | null;
  explanation: string | null;
  citations: string[] | null;
};

export type Fixture = {
  fixture_id: string;
  ticker: string;
  company: string;
  description: string;
  move_date: string;
  pct_change: number;
  catalyst_present: boolean;
  ground_truth: string;
  has_filing: boolean;
  prices: { date: string; close: number }[];
};

export type RetrievedChunk = {
  doc_role: string;
  doc_name: string;
  filed_date: string;
  score: number;
  text: string;
};

export type ExplainResult = {
  catalyst_found: boolean;
  explanation: string;
  citations: string[];
  trigger_id?: number;
  cached?: boolean;
  input_tokens?: number;
  output_tokens?: number;
  model?: string | null;
};

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path} failed: ${res.status}`);
  return res.json();
}

export const getHealth = () =>
  getJSON<{
    status: string;
    corpus_chunks: number;
    watchlist: string[];
    threshold: number;
  }>("/api/health");

export const getWatchlist = () =>
  getJSON<{ threshold: number; quotes: Quote[] }>("/api/watchlist");

export const getTriggers = () =>
  getJSON<{ triggers: Trigger[] }>("/api/triggers?limit=25");

export const getFixtures = () => getJSON<{ fixtures: Fixture[] }>("/api/fixtures");

export const getUsage = () =>
  getJSON<{ explanations: number; input_tokens: number; output_tokens: number }>(
    "/api/usage",
  );

export const getCorpus = () =>
  getJSON<{
    total_chunks: number;
    filings: {
      ticker: string;
      source_id: string;
      filed_date: string;
      chunks: number;
      documents: number;
    }[];
  }>("/api/corpus");

/**
 * Streams an explanation over SSE.
 *
 * The browser's EventSource cannot POST, so the stream is read straight
 * off the fetch body and the `event:`/`data:` frames are parsed here.
 * A cache hit arrives as a single `result` frame with no deltas.
 */
export async function streamExplain(
  body: { ticker: string; move_date: string; pct_change: number; force?: boolean },
  handlers: {
    onRetrieved?: (chunks: RetrievedChunk[]) => void;
    onDelta?: (text: string) => void;
    onResult?: (result: ExplainResult) => void;
    onError?: (message: string) => void;
  },
) {
  const res = await fetch(`${API_BASE}/api/explain/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.body) {
    handlers.onError?.("no response body");
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE frames are separated by a blank line.
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";

    for (const frame of frames) {
      const eventLine = frame.split("\n").find((l) => l.startsWith("event: "));
      const dataLine = frame.split("\n").find((l) => l.startsWith("data: "));
      if (!eventLine || !dataLine) continue;

      const event = eventLine.slice(7).trim();
      let data: unknown;
      try {
        data = JSON.parse(dataLine.slice(6));
      } catch {
        continue;
      }

      if (event === "retrieved") handlers.onRetrieved?.(data as RetrievedChunk[]);
      else if (event === "delta") handlers.onDelta?.(data as string);
      else if (event === "result") handlers.onResult?.(data as ExplainResult);
      else if (event === "error")
        handlers.onError?.((data as { message: string }).message);
    }
  }
}
