import { useState, type FormEvent } from "react";
import { useAddObserver, useObservers, useRemoveObserver } from "../api/hooks";
import type { Observer } from "../api/schemas";
import { Badge, Button, Checkbox, ConfirmDialog, Dialog, EmptyState, ErrorState, Field, Input, Loading, PageHeader, Table, Td } from "../components/ui";
import { splitList } from "../lib/config";
import { useRun } from "../lib/useRun";

const EVENTS = [
  "deploy.started",
  "deploy.succeeded",
  "deploy.failed",
  "approval.requested",
  "approval.approved",
  "approval.rejected",
  "model.sleeping",
  "model.woke",
  "model.waiting_for_gpu",
  "model.removed",
];

function isHttpUrl(value: string): boolean {
  try {
    const u = new URL(value);
    return (u.protocol === "http:" || u.protocol === "https:") && !!u.hostname;
  } catch {
    return false;
  }
}

function AddForm({ onClose }: { onClose: () => void }) {
  const add = useAddObserver();
  const run = useRun();
  const [url, setUrl] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [custom, setCustom] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!isHttpUrl(url.trim())) return setError("Enter a full http:// or https:// URL.");
    const events = [...selected, ...splitList(custom)];
    if (events.length === 0) return setError("Pick at least one event.");
    setError(null);
    const res = await run(add.mutateAsync({ webhook_url: url.trim(), event_types: [...new Set(events)] }), "Observer added");
    if (res) onClose();
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      <Field label="Webhook URL" hint="Receives a POST for each subscribed event.">
        {(p) => <Input {...p} type="url" required value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://example.com/hooks/mlapi" autoFocus />}
      </Field>
      <fieldset className="space-y-2">
        <legend className="mb-1 text-sm font-medium">Events</legend>
        <div className="grid gap-2 sm:grid-cols-2">
          {EVENTS.map((ev) => (
            <Checkbox
              key={ev}
              label={<code className="text-xs">{ev}</code>}
              checked={selected.has(ev)}
              onChange={(e) =>
                setSelected((s) => {
                  const next = new Set(s);
                  if (e.target.checked) next.add(ev);
                  else next.delete(ev);
                  return next;
                })
              }
            />
          ))}
        </div>
      </fieldset>
      <Field label="Other events (optional)" hint="Space or comma separated.">
        {(p) => <Input {...p} value={custom} onChange={(e) => setCustom(e.target.value)} />}
      </Field>
      {error && (
        <p role="alert" className="border-l-2 border-ink pl-3 text-sm font-medium">
          {error}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <Button onClick={onClose}>Cancel</Button>
        <Button type="submit" variant="primary" busy={add.isPending}>
          Add observer
        </Button>
      </div>
    </form>
  );
}

export function ObserversPage() {
  const list = useObservers();
  const remove = useRemoveObserver();
  const run = useRun();
  const [adding, setAdding] = useState(false);
  const [removing, setRemoving] = useState<Observer | null>(null);

  return (
    <>
      <PageHeader
        title="Observers"
        subtitle="Webhooks notified about deploys, approvals and sleep/wake events. An observer that keeps failing is removed automatically."
        actions={
          <Button variant="primary" onClick={() => setAdding(true)}>
            Add observer
          </Button>
        }
      />
      {list.isPending ? (
        <Loading />
      ) : list.isError ? (
        <ErrorState error={list.error} retry={() => void list.refetch()} />
      ) : list.data.length === 0 ? (
        <EmptyState title="No observers" hint="Add a webhook to get notified when models deploy or need approval." />
      ) : (
        <Table head={["Webhook", "Events", "Failures", ""]}>
          {list.data.map((o) => (
            <tr key={o.id}>
              <Td className="max-w-xs font-mono text-xs break-all">{o.webhook_url}</Td>
              <Td><span className="flex flex-wrap gap-1">{o.event_types.map((e) => <Badge key={e}>{e}</Badge>)}</span></Td>
              <Td>{o.connection_errors_count > 0 ? <Badge tone="amber">{o.connection_errors_count}</Badge> : "0"}</Td>
              <Td className="text-right">
                <Button variant="danger" onClick={() => setRemoving(o)} aria-label={`Remove ${o.webhook_url}`}>
                  Remove
                </Button>
              </Td>
            </tr>
          ))}
        </Table>
      )}
      <Dialog open={adding} onClose={() => setAdding(false)} title="Add observer" wide>
        {adding && <AddForm onClose={() => setAdding(false)} />}
      </Dialog>
      <ConfirmDialog
        open={!!removing}
        onClose={() => setRemoving(null)}
        title="Remove observer?"
        body={<>It stops receiving notifications at <code className="break-all">{removing?.webhook_url}</code>.</>}
        confirmLabel="Remove"
        busy={remove.isPending}
        onConfirm={async () => {
          if (removing && (await run(remove.mutateAsync(removing.id), "Observer removed"))) setRemoving(null);
        }}
      />
    </>
  );
}
