import { useState, type FormEvent } from "react";
import { useCreateKey, useKeys, useModels, usePatchKey, useRevokeKey } from "../api/hooks";
import type { ApiKey } from "../api/schemas";
import {
  Badge,
  Button,
  Checkbox,
  ConfirmDialog,
  CopyButton,
  Dialog,
  EmptyState,
  ErrorState,
  Field,
  Input,
  Loading,
  Mono,
  PageHeader,
  Table,
  Td,
} from "../components/ui";
import { splitList } from "../lib/config";
import { formatTime } from "../lib/format";
import { useRun } from "../lib/useRun";

const PATTERN = /^[a-z0-9][-a-z0-9]{0,38}\*?$/;

/** `<input type=date>` value (local day) -> unix seconds at the end of that day. */
const dateToExpiry = (value: string): number | null => (value ? Math.floor(new Date(`${value}T23:59:59`).getTime() / 1000) : null);
const expiryToDate = (unix: number | null): string => {
  if (!unix) return "";
  const d = new Date(unix * 1000);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};

function keyStatus(k: ApiKey, now = Date.now() / 1000): { label: string; tone: "green" | "red" | "amber" } {
  if (k.revoked_at) return { label: "revoked", tone: "red" };
  if (k.expires_at && k.expires_at <= now) return { label: "expired", tone: "amber" };
  return { label: "active", tone: "green" };
}

function KeyForm({ existing, onClose, onCreated }: { existing?: ApiKey; onClose: () => void; onCreated?: (plaintext: string) => void }) {
  const create = useCreateKey();
  const patch = usePatchKey();
  const run = useRun();
  const models = useModels();
  const [name, setName] = useState(existing?.name ?? "");
  const [all, setAll] = useState(existing?.allow_all ?? false);
  const [patterns, setPatterns] = useState((existing?.allowed_models ?? []).join(", "));
  const [expires, setExpires] = useState(expiryToDate(existing?.expires_at ?? null));
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const list = all ? [] : splitList(patterns);
    const bad = list.find((p) => !PATTERN.test(p));
    if (bad) return setError(`"${bad}" is not a valid model name. Use lowercase letters, digits and dashes, with an optional trailing *.`);
    if (!all && list.length === 0) return setError("Allow at least one model, or allow all models.");
    const expiry = dateToExpiry(expires);
    if (expiry !== null && expiry <= Date.now() / 1000) return setError("The expiry date must be in the future.");
    setError(null);

    if (existing) {
      const res = await run(
        patch.mutateAsync({ id: existing.id, patch: { name: name.trim(), allow_all: all, allowed_models: list, expires_at: expiry } }),
        "Key updated",
      );
      if (res) onClose();
    } else {
      const res = await run(
        create.mutateAsync({ name: name.trim(), allow_all: all, allowed_models: list, expires_at: expiry }),
        "Key created",
      );
      if (res) onCreated?.(res.value.key);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      <Field label="Name" hint="Who or what uses this key.">
        {(p) => <Input {...p} required maxLength={100} value={name} onChange={(e) => setName(e.target.value)} autoFocus />}
      </Field>
      <Checkbox label="Allow all models" checked={all} onChange={(e) => setAll(e.target.checked)} />
      {!all && (
        <Field label="Allowed models" hint="Comma or space separated. A trailing * matches a prefix, e.g. ecg-*">
          {(p) => (
            <>
              <Input {...p} list="model-names" value={patterns} onChange={(e) => setPatterns(e.target.value)} placeholder="xray, ecg-*" />
              <datalist id="model-names">{models.data?.filter((m) => m.state !== "removed").map((m) => <option key={m.name} value={m.name} />)}</datalist>
            </>
          )}
        </Field>
      )}
      <Field label="Expires (optional)" hint="Last day the key works. Leave empty for no expiry.">
        {(p) => <Input {...p} type="date" value={expires} onChange={(e) => setExpires(e.target.value)} />}
      </Field>
      {error && (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          {error}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <Button onClick={onClose}>Cancel</Button>
        <Button type="submit" variant="primary" busy={create.isPending || patch.isPending} disabled={!name.trim()}>
          {existing ? "Save" : "Create key"}
        </Button>
      </div>
    </form>
  );
}

export function KeysPage() {
  const keys = useKeys();
  const revoke = useRevokeKey();
  const run = useRun();
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<ApiKey | null>(null);
  const [revoking, setRevoking] = useState<ApiKey | null>(null);
  const [plaintext, setPlaintext] = useState<string | null>(null);
  const [showRevoked, setShowRevoked] = useState(false);

  const rows = (keys.data ?? []).filter((k) => showRevoked || !k.revoked_at);

  return (
    <>
      <PageHeader
        title="API keys"
        subtitle="Keys clients use with the Router (X-API-Key). Each key is scoped to specific models."
        actions={
          <Button variant="primary" onClick={() => setCreating(true)}>
            Create key
          </Button>
        }
      />
      <div className="mb-4">
        <Checkbox label="Show revoked keys" checked={showRevoked} onChange={(e) => setShowRevoked(e.target.checked)} />
      </div>
      {keys.isPending ? (
        <Loading />
      ) : keys.isError ? (
        <ErrorState error={keys.error} retry={() => void keys.refetch()} />
      ) : rows.length === 0 ? (
        <EmptyState title="No API keys" hint="Create a key to let a client call the Router." />
      ) : (
        <Table head={["Name", "ID", "Models", "Status", "Expires", "Created", ""]}>
          {rows.map((k) => {
            const s = keyStatus(k);
            return (
              <tr key={k.id}>
                <Td className="font-medium">{k.name}</Td>
                <Td><Mono>{k.id}</Mono></Td>
                <Td>
                  {k.allow_all ? (
                    <Badge tone="amber">all models</Badge>
                  ) : (
                    <span className="flex max-w-xs flex-wrap gap-1">{k.allowed_models.map((m) => <Badge key={m}>{m}</Badge>)}</span>
                  )}
                </Td>
                <Td><Badge tone={s.tone}>{s.label}</Badge></Td>
                <Td className="whitespace-nowrap">{k.expires_at ? formatTime(k.expires_at) : "never"}</Td>
                <Td className="whitespace-nowrap">{formatTime(k.created_at)}</Td>
                <Td className="text-right whitespace-nowrap">
                  {!k.revoked_at && (
                    <span className="flex justify-end gap-2">
                      <Button onClick={() => setEditing(k)} aria-label={`Edit ${k.name}`}>Edit</Button>
                      <Button variant="danger" onClick={() => setRevoking(k)} aria-label={`Revoke ${k.name}`}>Revoke</Button>
                    </span>
                  )}
                </Td>
              </tr>
            );
          })}
        </Table>
      )}

      <Dialog open={creating} onClose={() => setCreating(false)} title="Create API key">
        {creating && (
          <KeyForm
            onClose={() => setCreating(false)}
            onCreated={(key) => {
              setCreating(false);
              setPlaintext(key);
            }}
          />
        )}
      </Dialog>
      <Dialog open={!!editing} onClose={() => setEditing(null)} title={editing ? `Edit ${editing.name}` : "Edit key"}>
        {editing && <KeyForm key={editing.id} existing={editing} onClose={() => setEditing(null)} />}
      </Dialog>

      {/* The plaintext exists only in this component's state and disappears when the dialog closes. */}
      <Dialog open={plaintext !== null} onClose={() => setPlaintext(null)} title="Copy your new key">
        <div className="space-y-4">
          <p className="text-sm text-amber-800 dark:text-amber-300">
            This is the only time the key is shown. Store it somewhere safe; if you lose it, create a new one.
          </p>
          <pre data-testid="new-key" className="rounded-lg bg-slate-100 p-3 text-sm break-all whitespace-pre-wrap select-all dark:bg-slate-950">{plaintext}</pre>
          <div className="flex justify-end gap-2">
            <CopyButton text={plaintext ?? ""} label="Copy key" />
            <Button variant="primary" onClick={() => setPlaintext(null)}>
              I have saved it
            </Button>
          </div>
        </div>
      </Dialog>

      <ConfirmDialog
        open={!!revoking}
        onClose={() => setRevoking(null)}
        title="Revoke this key?"
        body={<>Clients using <strong>{revoking?.name}</strong> stop working immediately. This cannot be undone.</>}
        typeToConfirm={revoking?.name}
        confirmLabel="Revoke key"
        busy={revoke.isPending}
        onConfirm={async () => {
          if (revoking && (await run(revoke.mutateAsync(revoking.id), "Key revoked"))) setRevoking(null);
        }}
      />
    </>
  );
}
