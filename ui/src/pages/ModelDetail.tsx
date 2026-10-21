import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError } from "../api/client";
import { useDeleteModel, useModel, useModelAction, usePatchConfig } from "../api/hooks";
import type { ModelDetail } from "../api/schemas";
import { Badge, Button, Card, Checkbox, ConfirmDialog, ErrorState, Field, Input, inputClass, Loading, Mono, PageHeader, StateBadge, Table, Td } from "../components/ui";
import { ModelLimits } from "../components/ModelLimits";
import { ResourceTable } from "../components/ResourceTable";
import { UsagePanel } from "../components/UsagePanel";
import { useToast } from "../components/toast";
import { envToText, parseEnv, splitList } from "../lib/config";
import { formatTime, shortDigest } from "../lib/format";
import { BUSY } from "../lib/state";
import { useRun } from "../lib/useRun";

function ConfigEditor({ model }: { model: ModelDetail }) {
  const patch = usePatchConfig();
  const run = useRun();
  const toast = useToast();
  const cfg = model.config;
  const [env, setEnv] = useState(envToText(cfg.env));
  const [secrets, setSecrets] = useState((cfg.secret_refs ?? []).join("\n"));
  const [idle, setIdle] = useState(String(cfg.idle_timeout_s ?? ""));
  const [maxJob, setMaxJob] = useState(String(cfg.max_job_seconds ?? ""));
  const [error, setError] = useState<string | null>(null);

  async function save(e: FormEvent) {
    e.preventDefault();
    const parsed = parseEnv(env);
    if ("error" in parsed) return setError(parsed.error);
    setError(null);
    const body: Record<string, unknown> = { env: parsed.env, secret_refs: splitList(secrets) };
    if (idle.trim() !== "") body.idle_timeout_s = Number(idle);
    if (model.mode === "queue" && maxJob.trim() !== "") body.max_job_seconds = Number(maxJob);
    const result = await run(patch.mutateAsync({ name: model.name, patch: body }), "Configuration saved");
    if (result?.value.redeploy) toast("success", "Environment changed: redeploying");
  }

  return (
    <form onSubmit={save} className="space-y-4">
      <Field label="Environment variables" hint="One KEY=VALUE per line. Changing these redeploys a running model." error={error}>
        {(p) => <textarea {...p} rows={5} className={`${inputClass} font-mono`} value={env} onChange={(e) => setEnv(e.target.value)} spellCheck={false} />}
      </Field>
      <Field label="Secret references" hint="Names of Kubernetes secrets, one per line.">
        {(p) => <textarea {...p} rows={2} className={`${inputClass} font-mono`} value={secrets} onChange={(e) => setSecrets(e.target.value)} spellCheck={false} />}
      </Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Idle timeout (seconds)" hint="0 = never sleep.">
          {(p) => <Input {...p} type="number" min={0} step={1} value={idle} onChange={(e) => setIdle(e.target.value)} />}
        </Field>
        {model.mode === "queue" && (
          <Field label="Max job seconds" hint="How long a stopping worker may finish its job.">
            {(p) => <Input {...p} type="number" min={1} step={1} value={maxJob} onChange={(e) => setMaxJob(e.target.value)} />}
          </Field>
        )}
      </div>
      <div className="flex justify-end">
        <Button type="submit" variant="primary" busy={patch.isPending}>
          Save configuration
        </Button>
      </div>
    </form>
  );
}

export function ModelDetailPage() {
  const { name = "" } = useParams();
  const navigate = useNavigate();
  const query = useModel(name);
  const action = useModelAction();
  const del = useDeleteModel();
  const run = useRun();
  const [confirm, setConfirm] = useState<null | "rollback" | "delete">(null);
  const [keepCache, setKeepCache] = useState(true);

  if (query.isPending) return <Loading />;
  if (query.isError) {
    const gone = query.error instanceof ApiError && query.error.status === 404;
    return gone ? (
      <ErrorState error={new ApiError(404, `Model "${name}" does not exist.`)} />
    ) : (
      <ErrorState error={query.error} retry={() => void query.refetch()} />
    );
  }
  const m = query.data;
  const busy = BUSY.has(m.state);
  const removed = m.state === "removed";
  const go = (a: "redeploy" | "rollback" | "sleep" | "wake", msg: string) =>
    run(action.mutateAsync({ name: m.name, action: a }), msg);

  return (
    <>
      <p className="mb-6 text-sm">
        <Link to="/models" className="text-mute transition-colors hover:text-ink">← Models</Link>
      </p>
      <PageHeader
        title={
          <span className="flex flex-wrap items-center gap-3">
            {m.name} <StateBadge state={m.state} /> <Badge>{m.mode}</Badge>
          </span>
        }
        subtitle={<Mono>{m.image}</Mono>}
        actions={
          !removed && (
            <>
              <Button disabled={m.state !== "sleeping"} onClick={() => void go("wake", "Waking model")}>Wake</Button>
              <Button disabled={m.state !== "ready"} onClick={() => void go("sleep", "Model put to sleep")}>Sleep</Button>
              <Button disabled={busy || !m.current_digest} onClick={() => void go("redeploy", "Redeploy started")}>Redeploy</Button>
              <Button disabled={busy || !m.previous_digest} onClick={() => setConfirm("rollback")}>Roll back</Button>
              <Button variant="danger" disabled={busy} onClick={() => setConfirm("delete")}>Delete</Button>
            </>
          )
        }
      />

      {m.state_detail && (
        <p role="status" className="mb-8 rounded-lg border border-dashed border-rule bg-raised px-4 py-3 text-sm">
          {m.state_detail}
        </p>
      )}
      {m.pending_digest && m.state !== "pending_approval" && (
        <p className="mb-8 rounded-lg border border-dashed border-rule bg-raised px-4 py-3 text-sm">
          A newer version (<Mono>{shortDigest(m.pending_digest)}</Mono>) requests more resources. The current version keeps running.{" "}
          <Link to="/approvals" className="font-medium underline">Review approval</Link>
        </p>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Runtime">
          <dl className="grid grid-cols-[auto_1fr] gap-x-8 gap-y-2.5 text-sm">
            <dt className="text-mute">Current digest</dt><dd><Mono>{shortDigest(m.current_digest)}</Mono></dd>
            <dt className="text-mute">Previous digest</dt><dd><Mono>{shortDigest(m.previous_digest)}</Mono></dd>
            {m.failed_digest && (<><dt className="text-mute">Failed digest</dt><dd><Mono>{shortDigest(m.failed_digest)}</Mono></dd></>)}
            <dt className="text-mute">Source</dt><dd>{m.source}</dd>
            {m.runtime ? (
              <>
                <dt className="text-mute">Pods ready</dt><dd>{m.runtime.ready ? "yes" : "no"} ({m.runtime.replicas} replica{m.runtime.replicas === 1 ? "" : "s"})</dd>
                <dt className="text-mute">Restarts</dt><dd className={m.runtime.restarts > 0 ? "font-semibold" : ""}>{m.runtime.restarts}</dd>
                {m.runtime.pending_reason && (<><dt className="text-mute">Pending reason</dt><dd>{m.runtime.pending_reason}</dd></>)}
              </>
            ) : (
              <><dt className="text-mute">Runtime</dt><dd className="text-mute">status unavailable</dd></>
            )}
            {m.worker_state && (<><dt className="text-mute">Worker</dt><dd>{m.worker_state}</dd></>)}
            <dt className="text-mute">Last checked</dt><dd>{formatTime(m.last_checked_at)}</dd>
            <dt className="text-mute">Created</dt><dd>{formatTime(m.created_at)}</dd>
          </dl>
        </Card>
        <Card title="Resources">
          <ResourceTable approved={m.approved_resources} requested={m.requested_resources} />
        </Card>
        {m.state !== "removed" && m.state !== "pending_approval" && <UsagePanel model={m} />}
        {!removed && <ModelLimits key={JSON.stringify(m.config.limits ?? {})} model={m} />}
        <Card title="Configuration" className="lg:col-span-2">
          {removed ? <p className="text-sm text-mute">This model was removed.</p> : <ConfigEditor key={m.updated_at} model={m} />}
        </Card>
      </div>

      <h2 className="mt-16 mb-4 text-2xl font-semibold tracking-[-0.025em]">Deploy history</h2>
      {m.deployments.length === 0 ? (
        <p className="text-sm text-mute">No deployments yet.</p>
      ) : (
        <Table head={["Started", "Digest", "Result", "Finished", "Reason"]}>
          {m.deployments.map((d, i) => (
            <tr key={`${d.digest}-${d.started_at}-${i}`}>
              <Td className="whitespace-nowrap">{formatTime(d.started_at)}</Td>
              <Td><Mono>{shortDigest(d.digest)}</Mono></Td>
              <Td><Badge tone={d.status === "succeeded" ? "green" : d.status === "failed" ? "red" : "amber"}>{d.status.replace("_", " ")}</Badge></Td>
              <Td className="whitespace-nowrap">{formatTime(d.finished_at)}</Td>
              <Td className="max-w-sm text-xs text-mute">{d.reason ?? "—"}</Td>
            </tr>
          ))}
        </Table>
      )}

      {m.schema != null && (
        <details className="mt-10 rounded-lg bg-raised p-4">
          <summary className="cursor-pointer text-sm font-semibold">Request / response schema</summary>
          <pre className="mt-3 max-h-96 overflow-auto rounded-md bg-sunken p-3 font-mono text-xs">{JSON.stringify(m.schema, null, 2)}</pre>
        </details>
      )}

      <ConfirmDialog
        open={confirm === "rollback"}
        onClose={() => setConfirm(null)}
        title="Roll back to previous version?"
        body={<>Redeploys <Mono>{shortDigest(m.previous_digest)}</Mono>. The current version <Mono>{shortDigest(m.current_digest)}</Mono> is replaced.</>}
        confirmLabel="Roll back"
        danger={false}
        busy={action.isPending}
        onConfirm={async () => {
          if (await go("rollback", "Rollback started")) setConfirm(null);
        }}
      />
      <ConfirmDialog
        open={confirm === "delete"}
        onClose={() => setConfirm(null)}
        title={`Delete ${m.name}?`}
        body="The model is stopped and removed from routing. API keys that referenced it keep working for other models."
        typeToConfirm={m.name}
        confirmLabel="Delete model"
        busy={del.isPending}
        onConfirm={async () => {
          if (await run(del.mutateAsync({ name: m.name, keepCache }), "Model deleted")) {
            setConfirm(null);
            navigate("/models");
          }
        }}
      >
        <Checkbox label="Keep the weight cache volume" checked={keepCache} onChange={(e) => setKeepCache(e.target.checked)} />
      </ConfirmDialog>
    </>
  );
}
