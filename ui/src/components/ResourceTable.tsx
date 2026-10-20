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
      <thead className="text-left text-xs text-slate-500">
        <tr>
          <th scope="col" className="pb-2 font-medium" />
          <th scope="col" className="pb-2 font-medium">Approved</th>
          <th scope="col" className="pb-2 font-medium">Requested by image</th>
        </tr>
      </thead>
      <tbody>
        {ROWS.map((r) => (
          <tr key={r.key} className="border-t border-slate-100 dark:border-slate-800">
            <th scope="row" className="py-2 pr-4 text-left font-normal text-slate-600 dark:text-slate-400">{r.label}</th>
            <td className="py-2 pr-4 tabular-nums">{approved ? r.show(approved) : "—"}</td>
            <td className={cx("py-2 tabular-nums", over.has(r.key) && "font-semibold text-amber-700 dark:text-amber-400")}>
              {requested ? r.show(requested) : "—"}
              {over.has(r.key) && <span className="ml-2 text-xs">needs approval</span>}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
