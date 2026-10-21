import { useState } from "react";
import { useKeyUsage } from "../api/hooks";
import type { ApiKey } from "../api/schemas";
import { usageTotals } from "../lib/limits";
import { formatTime } from "../lib/format";
import { cx, Dialog, ErrorState, Loading, Mono, Table, Td } from "./ui";

const SPANS = [
  { days: 1, label: "Today" },
  { days: 7, label: "7 days" },
  { days: 30, label: "30 days" },
] as const;

const UNIDENTIFIED = "-"; // how the Router counts requests that ended before a model was identified

function UsageBody({ apiKey }: { apiKey: ApiKey }) {
  const [days, setDays] = useState<number>(7);
  const query = useKeyUsage(apiKey.id, days);
  const rows = query.data ? usageTotals(query.data.days) : [];
  const unidentified = rows.some((r) => r.model === UNIDENTIFIED);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-mute">
          Last used: {query.data ? (query.data.last_used ? formatTime(query.data.last_used) : "never in this period") : "…"}
        </p>
        <div role="group" aria-label="Period" className="inline-flex overflow-hidden rounded-md border border-hairline">
          {SPANS.map((s) => (
            <button
              key={s.days}
              type="button"
              aria-pressed={days === s.days}
              onClick={() => setDays(s.days)}
              className={cx("px-3 py-1 text-xs font-medium transition-colors", days === s.days ? "bg-ink text-canvas" : "text-mute hover:text-ink")}
            >
              {s.label}
            </button>
          ))}
        </div>
      </div>
      {query.isPending ? (
        <Loading />
      ) : query.isError ? (
        <ErrorState error={query.error} retry={() => void query.refetch()} />
      ) : rows.length === 0 ? (
        <p className="text-sm text-mute">No requests in this period.</p>
      ) : (
        <>
          <Table head={["Model", "Requests", "Model errors", "Refused (429)"]}>
            {rows.map((r) => (
              <tr key={r.model}>
                <Td className="font-medium">{r.model === UNIDENTIFIED ? <span className="text-mute">no model</span> : <Mono>{r.model}</Mono>}</Td>
                <Td>{r.requests}</Td>
                <Td>{r.errors}</Td>
                <Td>{r.limited}</Td>
              </tr>
            ))}
          </Table>
          {unidentified && <p className="text-xs text-mute">“No model” is requests that ended before a model was identified, for example a model the key may not use.</p>}
        </>
      )}
      <p className="text-xs text-mute">Counted by the Router per UTC day and kept for 35 days. “Refused” is requests answered 429 because a limit was reached.</p>
    </div>
  );
}

/** What one key has been used for, from the counters the Router keeps. */
export function KeyUsageDialog({ apiKey, onClose }: { apiKey: ApiKey | null; onClose: () => void }) {
  return (
    <Dialog open={!!apiKey} onClose={onClose} title={apiKey ? `Usage of ${apiKey.name}` : "Usage"} wide>
      {apiKey && <UsageBody key={apiKey.id} apiKey={apiKey} />}
    </Dialog>
  );
}
