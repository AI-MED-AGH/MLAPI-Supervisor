import { useMemo, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useModels, useRegisterModel } from "../api/hooks";
import { MODEL_STATES } from "../api/schemas";
import { formatBytes, formatCpu, shortDigest, timeAgo } from "../lib/format";
import { stateLabel } from "../lib/state";
import { useRun } from "../lib/useRun";
import {
  Badge,
  Button,
  Dialog,
  EmptyState,
  ErrorState,
  Field,
  inputClass,
  Input,
  Loading,
  Mono,
  PageHeader,
  StateBadge,
  Table,
  Td,
} from "../components/ui";

function RegisterDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const register = useRegisterModel();
  const run = useRun();
  const [image, setImage] = useState("");
  const [name, setName] = useState("");
  const [source, setSource] = useState<"local" | "ghcr">("local");

  async function submit(e: FormEvent) {
    e.preventDefault();
    const result = await run(
      register.mutateAsync({ image: image.trim(), name: name.trim() || undefined, source }),
      "Model registered",
    );
    if (result) {
      setImage("");
      setName("");
      onClose();
    }
  }

  return (
    <Dialog open={open} onClose={onClose} title="Register a model">
      <form onSubmit={submit} className="space-y-4">
        <p className="text-sm text-slate-600 dark:text-slate-400">
          The image must carry the <Mono>mlapi.model=true</Mono> label. Resources it requests need your approval before it is deployed.
        </p>
        <Field label="Image" hint="e.g. ghcr.io/ai-med-agh/pneumonia-xray:latest, or a local image tag">
          {(p) => <Input {...p} required value={image} onChange={(e) => setImage(e.target.value)} placeholder="ghcr.io/org/model" />}
        </Field>
        <Field label="Name (optional)" hint="Defaults to the name in the image labels.">
          {(p) => <Input {...p} value={name} onChange={(e) => setName(e.target.value)} pattern="[a-z0-9]([\-a-z0-9]{0,38}[a-z0-9])?" />}
        </Field>
        <Field label="Source">
          {(p) => (
            <select {...p} className={inputClass} value={source} onChange={(e) => setSource(e.target.value as "local" | "ghcr")}>
              <option value="local">Local image on this host</option>
              <option value="ghcr">Registry (GHCR)</option>
            </select>
          )}
        </Field>
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" busy={register.isPending} disabled={!image.trim()}>
            Register
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

export function ModelsPage() {
  const models = useModels();
  const [query, setQuery] = useState("");
  const [state, setState] = useState("");
  const [registering, setRegistering] = useState(false);

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (models.data ?? [])
      .filter((m) => m.state !== "removed" || state === "removed")
      .filter((m) => !state || m.state === state)
      .filter((m) => !q || m.name.toLowerCase().includes(q) || m.image.toLowerCase().includes(q))
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [models.data, query, state]);

  return (
    <>
      <PageHeader
        title="Models"
        subtitle="Deployed and pending model images."
        actions={
          <Button variant="primary" onClick={() => setRegistering(true)}>
            Register model
          </Button>
        }
      />
      <div className="mb-4 flex flex-wrap gap-3">
        <div className="min-w-48 flex-1">
          <Input aria-label="Search models" type="search" placeholder="Search name or image…" value={query} onChange={(e) => setQuery(e.target.value)} />
        </div>
        <select aria-label="Filter by state" className={`${inputClass} !w-auto`} value={state} onChange={(e) => setState(e.target.value)}>
          <option value="">All states</option>
          {MODEL_STATES.map((s) => (
            <option key={s} value={s}>
              {stateLabel(s)}
            </option>
          ))}
        </select>
      </div>

      {models.isPending ? (
        <Loading />
      ) : models.isError ? (
        <ErrorState error={models.error} retry={() => void models.refetch()} />
      ) : rows.length === 0 ? (
        <EmptyState
          title={models.data.length === 0 ? "No models registered" : "No models match"}
          hint={models.data.length === 0 ? "Register a model image to get started." : "Try a different search or state filter."}
        />
      ) : (
        <Table head={["Name", "State", "Mode", "Digest", "Approved resources", "Updated"]}>
          {rows.map((m) => (
            <tr key={m.name} className="hover:bg-slate-50 dark:hover:bg-slate-800/40">
              <Td>
                <Link to={`/models/${encodeURIComponent(m.name)}`} className="font-medium text-brand-700 hover:underline dark:text-brand-100">
                  {m.name}
                </Link>
                <p className="max-w-xs truncate text-xs text-slate-500" title={m.image}>
                  {m.image}
                </p>
              </Td>
              <Td>
                <StateBadge state={m.state} />
                {m.pending_digest && m.state !== "pending_approval" && (
                  <p className="mt-1 text-xs text-amber-700 dark:text-amber-400">update awaiting approval</p>
                )}
              </Td>
              <Td>
                <Badge>{m.mode}</Badge>
              </Td>
              <Td>
                <Mono>{shortDigest(m.current_digest)}</Mono>
              </Td>
              <Td className="text-xs whitespace-nowrap">
                {m.approved_resources
                  ? `${formatCpu(m.approved_resources.cpu_m)} CPU · ${formatBytes(m.approved_resources.memory_bytes)}${m.approved_resources.gpu ? " · GPU" : ""}`
                  : "—"}
              </Td>
              <Td className="text-xs whitespace-nowrap text-slate-500">{timeAgo(m.updated_at)}</Td>
            </tr>
          ))}
        </Table>
      )}
      <RegisterDialog open={registering} onClose={() => setRegistering(false)} />
    </>
  );
}
