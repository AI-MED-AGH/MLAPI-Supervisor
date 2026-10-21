import { Link } from "react-router-dom";
import { useApprovals, useHealth, useKeys, useModels } from "../api/hooks";
import { MODEL_STATES } from "../api/schemas";
import { stateLabel } from "../lib/state";
import { timeAgo } from "../lib/format";
import { Badge, Card, EmptyState, ErrorState, Loading, PageHeader, StateBadge } from "../components/ui";

function Stat({ label, value, to, attention }: { label: string; value: number | string; to: string; attention?: boolean }) {
  return (
    <Link to={to} className="group relative flex flex-col gap-6 px-0 py-6 transition-colors duration-150 hover:bg-raised sm:px-6 sm:first:pl-0">
      <span className="flex items-center gap-2 text-sm text-mute transition-colors group-hover:text-ink">
        {label}
        {attention && <span className="rounded-sm border border-dashed border-rule px-1.5 font-mono text-[11px] text-ink">review</span>}
      </span>
      <span className="font-mono text-6xl leading-none font-light tracking-[-0.06em] tabular-nums">{value}</span>
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
      <div className="grid divide-y divide-hairline border-y border-ink/70 sm:grid-cols-2 sm:divide-x sm:divide-y-0 lg:grid-cols-4">
        <Stat label="Models" value={list.length} to="/models" />
        <Stat label="Ready" value={list.filter((m) => m.state === "ready").length} to="/models" />
        <Stat label="Pending approvals" value={approvals.data?.length ?? "–"} to="/approvals" attention={(approvals.data?.length ?? 0) > 0} />
        <Stat label="Active API keys" value={activeKeys ?? "–"} to="/keys" />
      </div>

      <div className="mt-12 grid gap-6 lg:grid-cols-[5fr_7fr]">
        <Card title="Models by state">
          {counts.length === 0 ? (
            <EmptyState title="No models yet" hint="Register a model image or enable GHCR polling." action={<Link className="text-sm font-medium underline underline-offset-4" to="/models">Go to models</Link>} />
          ) : (
            <ul className="space-y-3">
              {counts.map(([state, n]) => (
                <li key={state} className="flex items-center justify-between">
                  <StateBadge state={state} />
                  <span className="font-mono text-sm tabular-nums" aria-label={`${n} ${stateLabel(state)}`}>{n}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title="Needs attention">
          {attention.length === 0 ? (
            <p className="text-sm text-mute">Nothing needs attention.</p>
          ) : (
            <ul className="divide-y divide-hairline">
              {attention.map((m) => (
                <li key={m.name} className="flex items-center justify-between gap-3 py-2.5">
                  <div className="min-w-0">
                    <Link to={`/models/${encodeURIComponent(m.name)}`} className="font-medium hover:underline">
                      {m.name}
                    </Link>
                    <p className="truncate text-xs text-mute">{m.state_detail ?? `updated ${timeAgo(m.updated_at)}`}</p>
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
