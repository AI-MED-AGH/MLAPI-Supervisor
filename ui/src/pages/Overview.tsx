import { Link } from "react-router-dom";
import { useApprovals, useHealth, useKeys, useModels } from "../api/hooks";
import { MODEL_STATES } from "../api/schemas";
import { stateLabel } from "../lib/state";
import { timeAgo } from "../lib/format";
import { Badge, Card, EmptyState, ErrorState, Loading, PageHeader, StateBadge } from "../components/ui";

function Stat({ label, value, to, tone }: { label: string; value: number | string; to: string; tone?: "warn" }) {
  return (
    <Link
      to={to}
      className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm transition-colors hover:border-brand-500 dark:border-slate-800 dark:bg-slate-900"
    >
      <p className="text-sm text-slate-600 dark:text-slate-400">{label}</p>
      <p className={`mt-1 text-3xl font-semibold ${tone === "warn" && Number(value) > 0 ? "text-amber-600" : ""}`}>{value}</p>
    </Link>
  );
}

export function OverviewPage() {
  const models = useModels();
  const approvals = useApprovals("pending");
  const keys = useKeys();
  const health = useHealth();

  if (models.isPending) return <Loading />;
  if (models.isError) return <ErrorState error={models.error} retry={() => void models.refetch()} />;

  const list = models.data.filter((m) => m.state !== "removed");
  const counts = MODEL_STATES.map((s) => [s, list.filter((m) => m.state === s).length] as const).filter(([, n]) => n > 0);
  const attention = list.filter((m) => m.state === "failed" || m.state === "waiting_for_gpu" || m.state === "pending_approval");
  const activeKeys = keys.data?.filter((k) => !k.revoked_at).length;

  return (
    <>
      <PageHeader
        title="Overview"
        subtitle="State of the platform at a glance."
        actions={
          <Badge tone={health.isError ? "red" : health.data ? "green" : "slate"}>
            {health.isError ? "Supervisor unreachable" : health.data ? "Supervisor healthy" : "Checking…"}
          </Badge>
        }
      />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Models" value={list.length} to="/models" />
        <Stat label="Ready" value={list.filter((m) => m.state === "ready").length} to="/models" />
        <Stat label="Pending approvals" value={approvals.data?.length ?? "–"} to="/approvals" tone="warn" />
        <Stat label="Active API keys" value={activeKeys ?? "–"} to="/keys" />
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <Card title="Models by state">
          {counts.length === 0 ? (
            <EmptyState title="No models yet" hint="Register a model image or enable GHCR polling." action={<Link className="text-sm text-brand-600 underline" to="/models">Go to models</Link>} />
          ) : (
            <ul className="space-y-2">
              {counts.map(([state, n]) => (
                <li key={state} className="flex items-center justify-between">
                  <StateBadge state={state} />
                  <span className="text-sm tabular-nums" aria-label={`${n} ${stateLabel(state)}`}>{n}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title="Needs attention">
          {attention.length === 0 ? (
            <p className="text-sm text-slate-600 dark:text-slate-400">Nothing needs attention.</p>
          ) : (
            <ul className="divide-y divide-slate-100 dark:divide-slate-800">
              {attention.map((m) => (
                <li key={m.name} className="flex items-center justify-between gap-3 py-2.5">
                  <div className="min-w-0">
                    <Link to={`/models/${encodeURIComponent(m.name)}`} className="font-medium hover:underline">
                      {m.name}
                    </Link>
                    <p className="truncate text-xs text-slate-500">{m.state_detail ?? `updated ${timeAgo(m.updated_at)}`}</p>
                  </div>
                  <StateBadge state={m.state} />
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </>
  );
}
