import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useApprovals, useApprove, useReject } from "../api/hooks";
import type { Approval } from "../api/schemas";
import { Badge, Button, Card, Checkbox, Dialog, EmptyState, ErrorState, Field, Input, Loading, Mono, PageHeader } from "../components/ui";
import { ResourceTable } from "../components/ResourceTable";
import { bytesToQuantity, cpuToQuantity, formatTime, parseCpu, parseQuantity, shortDigest } from "../lib/format";
import { useRun } from "../lib/useRun";

function ApproveDialog({ request, onClose }: { request: Approval | null; onClose: () => void }) {
  const approve = useApprove();
  const run = useRun();
  const r = request?.requested;
  const [cpu, setCpu] = useState("");
  const [memory, setMemory] = useState("");
  const [disk, setDisk] = useState("");
  const [gpu, setGpu] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [seen, setSeen] = useState<number | null>(null);

  // reset the form each time a different request is opened
  if (request && r && seen !== request.id) {
    setSeen(request.id);
    setCpu(cpuToQuantity(r.cpu_m));
    setMemory(bytesToQuantity(r.memory_bytes));
    setDisk(bytesToQuantity(r.disk_bytes));
    setGpu(r.gpu);
    setError(null);
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!request || !r) return;
    const c = parseCpu(cpu);
    const m = parseQuantity(memory);
    const d = parseQuantity(disk);
    if (c === null) return setError("CPU must look like 2, 0.5 or 500m.");
    if (m === null || d === null) return setError("Memory and disk must look like 512Mi or 2Gi.");
    if (c > r.cpu_m || m > r.memory_bytes || d > r.disk_bytes || (gpu && !r.gpu))
      return setError("You can approve at most what the image requested.");
    setError(null);
    const override: Record<string, string | boolean> = {};
    if (c !== r.cpu_m) override.cpu = cpu.trim();
    if (m !== r.memory_bytes) override.memory = memory.trim();
    if (d !== r.disk_bytes) override.disk = disk.trim();
    if (gpu !== r.gpu) override.gpu = gpu;
    const res = await run(approve.mutateAsync({ id: request.id, override }), `Approved ${request.model}`);
    if (res) onClose();
  }

  return (
    <Dialog open={!!request} onClose={onClose} title={request ? `Approve ${request.model}` : "Approve"}>
      <form onSubmit={submit} className="space-y-4">
        <p className="text-sm text-slate-600 dark:text-slate-400">
          Approving deploys <Mono>{shortDigest(request?.digest)}</Mono> with these limits. Lower the values to grant less than requested.
        </p>
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="CPU">{(p) => <Input {...p} value={cpu} onChange={(e) => setCpu(e.target.value)} />}</Field>
          <Field label="Memory">{(p) => <Input {...p} value={memory} onChange={(e) => setMemory(e.target.value)} />}</Field>
          <Field label="Disk">{(p) => <Input {...p} value={disk} onChange={(e) => setDisk(e.target.value)} />}</Field>
        </div>
        <Checkbox label="Allow GPU" checked={gpu} disabled={!r?.gpu} onChange={(e) => setGpu(e.target.checked)} />
        {error && (
          <p role="alert" className="text-sm text-red-600 dark:text-red-400">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" busy={approve.isPending}>
            Approve and deploy
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function RejectDialog({ request, onClose }: { request: Approval | null; onClose: () => void }) {
  const reject = useReject();
  const run = useRun();
  const [note, setNote] = useState("");
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!request) return;
    const res = await run(reject.mutateAsync({ id: request.id, note: note.trim() }), `Rejected ${request.model}`);
    if (res) {
      setNote("");
      onClose();
    }
  }
  return (
    <Dialog open={!!request} onClose={onClose} title={request ? `Reject ${request.model}` : "Reject"}>
      <form onSubmit={submit} className="space-y-4">
        <p className="text-sm text-slate-600 dark:text-slate-400">
          The running version is untouched. This digest will not be deployed or requested again.
        </p>
        <Field label="Note (optional)">
          {(p) => <Input {...p} maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} />}
        </Field>
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="danger" busy={reject.isPending}>
            Reject
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

const STATUS_TONE = { approved: "green", rejected: "red", superseded: "slate", pending: "amber" } as const;

export function ApprovalsPage() {
  const [scope, setScope] = useState<"pending" | "all">("pending");
  const list = useApprovals(scope);
  const [approving, setApproving] = useState<Approval | null>(null);
  const [rejecting, setRejecting] = useState<Approval | null>(null);

  return (
    <>
      <PageHeader
        title="Approvals"
        subtitle="Models request CPU, memory, disk and GPU through image labels. Pods only ever get what you approve."
        actions={
          <div role="group" aria-label="Show" className="inline-flex overflow-hidden rounded-lg border border-slate-300 dark:border-slate-700">
            {(["pending", "all"] as const).map((s) => (
              <button
                key={s}
                type="button"
                aria-pressed={scope === s}
                onClick={() => setScope(s)}
                className={`px-3.5 py-2 text-sm font-medium ${scope === s ? "bg-brand-600 text-white" : "bg-white hover:bg-slate-50 dark:bg-slate-900 dark:hover:bg-slate-800"}`}
              >
                {s === "pending" ? "Pending" : "History"}
              </button>
            ))}
          </div>
        }
      />
      {list.isPending ? (
        <Loading />
      ) : list.isError ? (
        <ErrorState error={list.error} retry={() => void list.refetch()} />
      ) : list.data.length === 0 ? (
        <EmptyState title={scope === "pending" ? "Nothing waiting for approval" : "No approval requests yet"} />
      ) : (
        <div className="space-y-4">
          {list.data.map((a) => (
            <Card
              key={a.id}
              title={
                <span className="flex flex-wrap items-center gap-2">
                  <Link to={`/models/${encodeURIComponent(a.model)}`} className="hover:underline">{a.model}</Link>
                  <Mono>{shortDigest(a.digest)}</Mono>
                  <Badge tone={STATUS_TONE[a.status as keyof typeof STATUS_TONE] ?? "slate"}>{a.status}</Badge>
                </span>
              }
              actions={
                a.status === "pending" && (
                  <div className="flex gap-2">
                    <Button variant="danger" onClick={() => setRejecting(a)}>Reject</Button>
                    <Button variant="primary" onClick={() => setApproving(a)}>Approve…</Button>
                  </div>
                )
              }
            >
              <ResourceTable approved={a.currently_approved} requested={a.requested} />
              <p className="mt-3 text-xs text-slate-500">
                Requested {formatTime(a.created_at)}
                {a.decided_at && ` · decided ${formatTime(a.decided_at)}${a.decided_by ? ` by ${a.decided_by}` : ""}`}
                {a.note && ` · “${a.note}”`}
              </p>
            </Card>
          ))}
        </div>
      )}
      <ApproveDialog request={approving} onClose={() => setApproving(null)} />
      <RejectDialog request={rejecting} onClose={() => setRejecting(null)} />
    </>
  );
}
