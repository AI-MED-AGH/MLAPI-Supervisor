import type { Resources } from "../api/schemas";
import { formatBytes, formatCpu } from "../lib/format";
import { exceeded } from "../lib/resources";
import { cx } from "./ui";

const ROWS: { key: keyof Resources; label: string; show: (r: Resources) => string }[] = [
  { key: "cpu_m", label: "CPU (cores)", show: (r) => formatCpu(r.cpu_m) },
  { key: "memory_bytes", label: "Memory", show: (r) => formatBytes(r.memory_bytes) },
  { key: "disk_bytes", label: "Disk", show: (r) => formatBytes(r.disk_bytes) },
  { key: "gpu", label: "GPU", show: (r) => (r.gpu ? "yes" : "no") },
];

/** Approved vs requested resources; the values that need approval are highlighted. */
export function ResourceTable({ approved, requested }: { approved: Resources | null; requested: Resources | null }) {
  const over = requested ? exceeded(requested, approved) : new Set<keyof Resources>();
  return (
    <table className="w-full text-sm">
      <thead className="text-left text-xs text-mute">
        <tr>
          <th scope="col" className="pb-2 font-medium" />
          <th scope="col" className="pb-2 font-medium">Approved</th>
          <th scope="col" className="pb-2 font-medium">Requested by image</th>
        </tr>
      </thead>
      <tbody>
        {ROWS.map((r) => (
          <tr key={r.key} className="border-t border-hairline">
            <th scope="row" className="py-2.5 pr-4 text-left font-normal text-mute">{r.label}</th>
            <td className="py-2.5 pr-4 font-mono tabular-nums">{approved ? r.show(approved) : "—"}</td>
            <td className={cx("py-2.5 font-mono tabular-nums", over.has(r.key) && "font-semibold")}>
              {requested ? r.show(requested) : "—"}
              {over.has(r.key) && <span className="ml-3 rounded-sm border border-dashed border-rule px-1.5 py-0.5 font-sans text-[11px] font-normal">needs approval</span>}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
