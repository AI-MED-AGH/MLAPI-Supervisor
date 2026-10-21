import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { TRANSITIONAL } from "../lib/state";
import { request } from "./client";
import {
  apiKey,
  apiKeyCreated,
  approval,
  approveResult,
  configResult,
  keyUsage,
  model,
  modelDetail,
  modelMetrics,
  observer,
  registerResult,
} from "./schemas";

const POLL_IDLE = 15_000;
const POLL_BUSY = 3_000;

export const qk = {
  models: ["models"] as const,
  model: (name: string) => ["models", name] as const,
  approvals: (status: string) => ["approvals", status] as const,
  keys: ["keys"] as const,
  observers: ["observers"] as const,
  health: ["health"] as const,
};

export const useModels = () =>
  useQuery({
    queryKey: qk.models,
    queryFn: ({ signal }) => request("GET", "/v1/models", z.array(model), { signal }),
    refetchInterval: (q) => (q.state.data?.some((m) => TRANSITIONAL.has(m.state)) ? POLL_BUSY : POLL_IDLE),
  });

export const useModel = (name: string) =>
  useQuery({
    queryKey: qk.model(name),
    queryFn: ({ signal }) => request("GET", `/v1/models/${encodeURIComponent(name)}`, modelDetail, { signal }),
    refetchInterval: (q) => (q.state.data && TRANSITIONAL.has(q.state.data.state) ? POLL_BUSY : POLL_IDLE),
  });

const METRICS_POLL: Record<string, number> = { "1h": 5_000, "24h": 30_000, "30d": 300_000, "1y": 300_000 };

/** Resource use of one model; the finer the range, the faster it refreshes. Keeps the old chart while reloading. */
export const useModelMetrics = (name: string, range: string) =>
  useQuery({
    queryKey: ["models", name, "metrics", range] as const,
    queryFn: ({ signal }) =>
      request("GET", `/v1/models/${encodeURIComponent(name)}/metrics?range=${range}`, modelMetrics, { signal }),
    placeholderData: keepPreviousData,
    refetchInterval: METRICS_POLL[range] ?? 30_000,
  });

export const useApprovals = (status: "pending" | "all") =>
  useQuery({
    queryKey: qk.approvals(status),
    queryFn: ({ signal }) => request("GET", `/v1/approvals?status_filter=${status}`, z.array(approval), { signal }),
    placeholderData: keepPreviousData,
    refetchInterval: POLL_IDLE,
  });

export const useKeys = () =>
  useQuery({ queryKey: qk.keys, queryFn: ({ signal }) => request("GET", "/v1/keys", z.array(apiKey), { signal }) });

/** What a key was used for over the last `days` days (the Router keeps 35). Only fetched while the dialog is open. */
export const useKeyUsage = (id: string, days: number) =>
  useQuery({
    queryKey: ["keys", id, "usage", days] as const,
    queryFn: ({ signal }) => request("GET", `/v1/keys/${id}/usage?days=${days}`, keyUsage, { signal }),
    placeholderData: keepPreviousData,
  });

export const useObservers = () =>
  useQuery({
    queryKey: qk.observers,
    queryFn: ({ signal }) => request("GET", "/v1/observers/", z.array(observer), { signal }),
  });

export const useHealth = () =>
  useQuery({
    queryKey: qk.health,
    queryFn: ({ signal }) =>
      request("GET", "/health", z.object({ status: z.string() }), { signal, skipUnauthorizedHandler: true }),
    refetchInterval: POLL_IDLE,
    retry: false,
  });

/** Every mutation can change models, approvals or keys; refetching all is cheap at this scale and never stale. */
function useAction<TVars, TData>(fn: (vars: TVars) => Promise<TData>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSettled: () => qc.invalidateQueries(),
  });
}

const modelPath = (name: string) => `/v1/models/${encodeURIComponent(name)}`;

export const useRegisterModel = () =>
  useAction((body: { image: string; name?: string; source: "local" | "ghcr" }) =>
    request("POST", "/v1/models", registerResult, { body }),
  );
export const useModelAction = () =>
  useAction(({ name, action }: { name: string; action: "redeploy" | "rollback" | "sleep" | "wake" }) =>
    request("POST", `${modelPath(name)}/${action}`, model),
  );
export const useDeleteModel = () =>
  useAction(({ name, keepCache }: { name: string; keepCache: boolean }) =>
    request("DELETE", `${modelPath(name)}?keep_cache=${keepCache}`, model),
  );
export const usePatchConfig = () =>
  useAction(({ name, patch }: { name: string; patch: Record<string, unknown> }) =>
    request("PATCH", `${modelPath(name)}/config`, configResult, { body: patch }),
  );
export const useApprove = () =>
  useAction(({ id, override }: { id: number; override?: { cpu?: string; memory?: string; gpu?: boolean; disk?: string } }) =>
    request("POST", `/v1/approvals/${id}/approve`, approveResult, { body: override ?? {} }),
  );
export const useReject = () =>
  useAction(({ id, note }: { id: number; note?: string }) =>
    request("POST", `/v1/approvals/${id}/reject`, model, { body: { note: note || null } }),
  );
export const useCreateKey = () =>
  useAction((body: { name: string; allowed_models: string[]; allow_all: boolean; expires_at?: number | null; limits?: Record<string, number> }) =>
    request("POST", "/v1/keys", apiKeyCreated, { body }),
  );
export const usePatchKey = () =>
  useAction(({ id, patch }: { id: string; patch: Record<string, unknown> }) =>
    request("PATCH", `/v1/keys/${id}`, apiKey, { body: patch }),
  );
export const useRevokeKey = () => useAction((id: string) => request("DELETE", `/v1/keys/${id}`, null));
export const useAddObserver = () =>
  useAction((body: { webhook_url: string; event_types: string[] }) => request("POST", "/v1/observers/", null, { body }));
export const useRemoveObserver = () => useAction((id: number) => request("DELETE", `/v1/observers/${id}`, null));
