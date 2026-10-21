import { useState } from "react";
import { useModelMetrics } from "../api/hooks";
import type { ModelDetail } from "../api/schemas";
import { Button, Card, cx, Loading } from "./ui";
import { UsageChart } from "./UsageChart";
import { formatBytes } from "../lib/format";
import { coresLabel, formatCores, formatCoresAxis, formatTooltipTime, RANGES, share, type RangeId } from "../lib/usage";

function Meter({ value }: { value: number | null }) {
  if (value == null) return null;
  return (
    <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-sunken" role="presentation">
      <div className="h-full rounded-full bg-ink" style={{ width: `${Math.max(2, value * 100)}%` }} />
    </div>
  );
}

function Now({ label, value, limit, running }: { label: string; value: string | null; limit: string | null; running: boolean }) {
  return (
    <div className="min-w-0">
      <div className="text-xs text-mute">{label}</div>
      <div className="mt-0.5 flex items-baseline gap-1.5">
        <span className="text-2xl font-semibold tracking-tight">{running && value ? value : "—"}</span>
        {limit && <span className="text-xs text-mute">of {limit}</span>}
      </div>
    </div>
  );
}

/** Live and historical CPU and memory use of one model. */
export function UsagePanel({ model }: { model: ModelDetail }) {
  const [range, setRange] = useState<RangeId>("1h");
  const query = useModelMetrics(model.name, range);
  const data = query.data;
  const refreshing = query.isFetching && query.isPlaceholderData;
  const window = RANGES.find((r) => r.id === range)!.seconds;
  const to = data ? (query.dataUpdatedAt ? query.dataUpdatedAt / 1000 : Date.now() / 1000) : Date.now() / 1000;

  const cpuLimit = data?.limits.cpu_m ?? null;
  const memLimit = data?.limits.memory_bytes ?? null;
  const live = data?.latest ?? null;

  return (
    <Card
      title="Resource use"
      className="lg:col-span-2"
      actions={
        <div role="group" aria-label="Time range" className="inline-flex overflow-hidden rounded-md border border-hairline">
          {RANGES.map((r) => (
            <button
              key={r.id}
              type="button"
              aria-pressed={range === r.id}
              onClick={() => setRange(r.id)}
              className={cx(
                "px-3 py-1 text-xs font-medium transition-colors",
                range === r.id ? "bg-ink text-canvas" : "text-mute hover:text-ink",
              )}
            >
              {r.label}
            </button>
          ))}
        </div>
      }
    >
      {query.isPending ? (
        <Loading label="Loading measurements…" />
      ) : query.isError || !data ? (
        <p className="text-sm text-mute">
          Could not load measurements.{" "}
          <Button variant="ghost" onClick={() => void query.refetch()}>Retry</Button>
        </p>
      ) : (
        <div className={cx("transition-opacity", refreshing && "opacity-60")}>
          <div className="mb-5 grid grid-cols-2 gap-6">
            <div>
              <Now label="CPU now" value={live ? formatCores(live.cpu_m) : null} limit={cpuLimit ? coresLabel(cpuLimit) : null} running={!!live} />
              <Meter value={live ? share(live.cpu_m, cpuLimit) : null} />
            </div>
            <div>
              <Now label="Memory now" value={live ? formatBytes(live.memory_bytes) : null} limit={memLimit ? formatBytes(memLimit) : null} running={!!live} />
              <Meter value={live ? share(live.memory_bytes, memLimit) : null} />
            </div>
          </div>
          {!live && <p className="mb-4 -mt-2 text-xs text-mute">Not running right now{model.state === "sleeping" ? " (asleep)" : ""}, so nothing is being measured.</p>}

          <div className="grid gap-8 sm:grid-cols-2">
            <UsageChart
              title="CPU (cores)"
              points={data.points}
              avg="cpu_m"
              peak="cpu_m_max"
              step={data.step_seconds}
              range={range}
              from={to - window}
              to={to}
              limit={cpuLimit}
              floor={100}
              format={coresLabel}
              formatAxis={formatCoresAxis}
              limitLabel={`limit ${cpuLimit ? formatCores(cpuLimit) : ""}`}
            />
            <UsageChart
              title="Memory"
              points={data.points}
              avg="memory_bytes"
              peak="memory_bytes_max"
              step={data.step_seconds}
              range={range}
              from={to - window}
              to={to}
              limit={memLimit}
              floor={64 * 1024 ** 2}
              binary
              format={formatBytes}
              formatAxis={(v) => (v === 0 ? "0" : formatBytes(v))}
              limitLabel={`limit ${memLimit ? formatBytes(memLimit) : ""}`}
            />
          </div>
          <p className="mt-4 text-xs text-mute">
            {data.step_seconds >= 60 ? "The line is the average and the shaded band reaches the peak. " : ""}
            A gap means the model was not running.
            {range === "1h" ? " Updated every 5 seconds." : ""}
          </p>

          <details className="mt-3">
            <summary className="cursor-pointer text-xs font-medium text-mute hover:text-ink">Table view</summary>
            <div className="mt-2 max-h-64 overflow-auto rounded-md bg-sunken">
              <table className="w-full text-left text-xs tabular-nums">
                <thead className="sticky top-0 bg-sunken text-mute">
                  <tr><th className="px-3 py-1.5 font-medium">Time</th><th className="px-3 py-1.5 font-medium">CPU avg</th><th className="px-3 py-1.5 font-medium">CPU peak</th><th className="px-3 py-1.5 font-medium">Memory avg</th><th className="px-3 py-1.5 font-medium">Memory peak</th></tr>
                </thead>
                <tbody>
                  {[...data.points].reverse().slice(0, 300).map((pt) => (
                    <tr key={pt.t} className="border-t border-hairline">
                      <td className="px-3 py-1">{formatTooltipTime(pt.t, range)}</td>
                      <td className="px-3 py-1">{formatCores(pt.cpu_m)}</td>
                      <td className="px-3 py-1">{formatCores(pt.cpu_m_max)}</td>
                      <td className="px-3 py-1">{formatBytes(pt.memory_bytes)}</td>
                      <td className="px-3 py-1">{formatBytes(pt.memory_bytes_max)}</td>
                    </tr>
                  ))}
                  {data.points.length === 0 && <tr><td colSpan={5} className="px-3 py-3 text-mute">No measurements in this range.</td></tr>}
                </tbody>
              </table>
            </div>
          </details>
        </div>
      )}
    </Card>
  );
}
