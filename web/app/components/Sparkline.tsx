"use client";

/** Minimal inline price chart. The final point is emphasized because it is
 * the move being explained. */
export default function Sparkline({
  points,
  width = 220,
  height = 56,
}: {
  points: { date: string; close: number }[];
  width?: number;
  height?: number;
}) {
  if (points.length < 2) return null;

  const closes = points.map((p) => p.close);
  const min = Math.min(...closes);
  const max = Math.max(...closes);
  const span = max - min || 1;
  const pad = 4;

  const coords = points.map((p, i) => {
    const x = pad + (i / (points.length - 1)) * (width - pad * 2);
    const y = height - pad - ((p.close - min) / span) * (height - pad * 2);
    return [x, y] as const;
  });

  const path = coords.map(([x, y], i) => `${i ? "L" : "M"}${x},${y}`).join(" ");
  const rising = closes[closes.length - 1] >= closes[0];
  const stroke = rising ? "var(--up)" : "var(--down)";
  const [lastX, lastY] = coords[coords.length - 1];

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={`Price from ${points[0].date} to ${points[points.length - 1].date}`}
      className="overflow-visible"
    >
      <path d={path} fill="none" stroke={stroke} strokeWidth={1.75} />
      <circle cx={lastX} cy={lastY} r={3} fill={stroke} />
    </svg>
  );
}
