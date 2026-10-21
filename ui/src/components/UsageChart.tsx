import { useId, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import type { UsagePoint } from "../api/schemas";
import { formatAxisTime, formatTooltipTime, niceCeil, niceCeilBytes, segments, xTicks, yTicks, type RangeId } from "../lib/usage";

const W = 640;
const H = 210;
const M = { left: 54, right: 12, top: 10, bottom: 26 };
const PLOT_W = W - M.left - M.right;
const PLOT_H = H - M.top - M.bottom;

type Props = {
  title: string;
  points: UsagePoint[];
  /** which fields of a point to plot: the average and its peak */
  avg: "cpu_m" | "memory_bytes";
  peak: "cpu_m_max" | "memory_bytes_max";
  step: number;
  range: RangeId;
  from: number;
  to: number;
  limit: number | null;
  /** the smallest top of the y axis, so an idle model does not fill the chart with noise */
  floor: number;
  /** round the axis in binary units (memory) instead of decimal ones */
  binary?: boolean;
  format: (v: number) => string;
  formatAxis?: (v: number) => string;
  limitLabel: string;
};

/**
 * One measure over time. The line is the average, the shaded band reaches up to the peak, and a gap means the model
 * was not running. The limit is drawn only when it fits the scale (otherwise the panel states it in words), so a
 * generous limit does not flatten the data.
 */
export function UsageChart(p: Props) {
  const [hover, setHover] = useState<number | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const fmtAxis = p.formatAxis ?? p.format;

  const peak = p.points.reduce((m, pt) => Math.max(m, pt[p.peak]), 0);
  const top = (p.binary ? niceCeilBytes : niceCeil)(Math.max(peak * 1.1, p.floor));
  const showLimit = p.limit != null && p.limit <= top;
  const x = (t: number) => M.left + ((t - p.from) / (p.to - p.from)) * PLOT_W;
  const y = (v: number) => M.top + PLOT_H - (Math.min(v, top) / top) * PLOT_H;
  const coarse = p.step > 5;
  const segs = segments(p.points, p.step);

  const line = (s: UsagePoint[]) => s.map((pt, i) => `${i ? "L" : "M"}${x(pt.t).toFixed(1)},${y(pt[p.avg]).toFixed(1)}`).join("");
  const wash = (s: UsagePoint[]) => `${line(s)}L${x(s[s.length - 1].t).toFixed(1)},${y(0)}L${x(s[0].t).toFixed(1)},${y(0)}Z`;
  const band = (s: UsagePoint[]) =>
    s.map((pt, i) => `${i ? "L" : "M"}${x(pt.t).toFixed(1)},${y(pt[p.peak]).toFixed(1)}`).join("") +
    [...s].reverse().map((pt) => `L${x(pt.t).toFixed(1)},${y(pt[p.avg]).toFixed(1)}`).join("") + "Z";

  function nearest(clientX: number): number | null {
    const el = box.current;
    if (!el || p.points.length === 0) return null;
    const rect = el.getBoundingClientRect();
    const t = p.from + (((clientX - rect.left) / rect.width) * W - M.left) / PLOT_W * (p.to - p.from);
    let best = 0;
    for (let i = 1; i < p.points.length; i++) if (Math.abs(p.points[i].t - t) < Math.abs(p.points[best].t - t)) best = i;
    return best;
  }
  const onMove = (e: PointerEvent) => setHover(nearest(e.clientX));
  const onKey = (e: KeyboardEvent) => {
    if (e.key === "Escape") return setHover(null);
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    const last = p.points.length - 1;
    if (last < 0) return;
    const delta = e.key === "ArrowLeft" ? -1 : 1;
    setHover((h) => (h == null ? (delta < 0 ? last : 0) : Math.min(last, Math.max(0, h + delta))));
  };

  const hp = hover != null ? p.points[hover] : null;
  const latest = p.points[p.points.length - 1];
  const summary = latest
    ? `${p.title} over the selected range. Latest ${p.format(latest[p.avg])}, peak ${p.format(peak)}.`
    : `${p.title}: no measurements in the selected range.`;

  return (
    <figure className="m-0">
      <figcaption id={titleId} className="mb-1 text-xs font-medium text-mute">{p.title}</figcaption>
      <div ref={box} className="relative">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="block w-full touch-pan-y select-none text-ink outline-offset-4"
          role="img"
          aria-labelledby={titleId}
          aria-description={summary}
          tabIndex={0}
          onPointerMove={onMove}
          onPointerLeave={() => setHover(null)}
          onKeyDown={onKey}
          onBlur={() => setHover(null)}
        >
          {yTicks(top).map((v) => (
            <g key={v}>
              <line x1={M.left} x2={W - M.right} y1={y(v)} y2={y(v)} className="stroke-hairline" strokeWidth={1} />
              <text x={M.left - 8} y={y(v)} textAnchor="end" dominantBaseline="middle" className="fill-mute text-[10px] tabular-nums">
                {fmtAxis(v)}
              </text>
            </g>
          ))}
          {xTicks(p.from, p.to).map((t, i, all) => (
            <text key={t} x={x(t)} y={H - 7} textAnchor={i === 0 ? "start" : i === all.length - 1 ? "end" : "middle"} className="fill-mute text-[10px]">
              {formatAxisTime(t, p.range)}
            </text>
          ))}

          {showLimit && (
            <g>
              <line x1={M.left} x2={W - M.right} y1={y(p.limit!)} y2={y(p.limit!)} className="stroke-rule" strokeWidth={1} />
              <text x={W - M.right - 2} y={y(p.limit!) - 4} textAnchor="end" className="fill-mute text-[10px]">{p.limitLabel}</text>
            </g>
          )}

          {segs.map((s, i) =>
            s.length === 1 ? (
              <circle key={i} cx={x(s[0].t)} cy={y(s[0][p.avg])} r={3} className="fill-current" />
            ) : (
              <g key={i}>
                {coarse && <path d={band(s)} className="fill-current" opacity={0.14} />}
                <path d={wash(s)} className="fill-current" opacity={0.08} />
                <path d={line(s)} fill="none" className="stroke-current" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
              </g>
            ),
          )}

          {hp && (
            <g pointerEvents="none">
              <line x1={x(hp.t)} x2={x(hp.t)} y1={M.top} y2={M.top + PLOT_H} className="stroke-rule" strokeWidth={1} />
              <circle cx={x(hp.t)} cy={y(hp[p.avg])} r={4.5} className="fill-current stroke-raised" strokeWidth={2} />
            </g>
          )}
        </svg>

        {p.points.length === 0 && (
          <p className="pointer-events-none absolute inset-x-0 top-[38%] text-center text-xs text-mute">No measurements in this range</p>
        )}

        {hp && (
          <div
            role="status"
            className="pointer-events-none absolute z-10 whitespace-nowrap rounded-md bg-ink px-2.5 py-1.5 text-xs text-canvas shadow-lg"
            style={{
              left: `clamp(4rem, ${(x(hp.t) / W) * 100}%, calc(100% - 4rem))`,
              top: `${(y(hp[p.avg]) / H) * 100}%`,
              transform: "translate(-50%, calc(-100% - 10px))",
            }}
          >
            <div className="text-[11px] opacity-70">{formatTooltipTime(hp.t, p.range)}</div>
            <div className="text-sm font-semibold tabular-nums">{p.format(hp[p.avg])}</div>
            {coarse && <div className="tabular-nums opacity-70">peak {p.format(hp[p.peak])}</div>}
          </div>
        )}
      </div>
    </figure>
  );
}
