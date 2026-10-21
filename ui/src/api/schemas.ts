import { z } from "zod";

export const resources = z.object({
  cpu_m: z.number(),
  memory_bytes: z.number(),
  gpu: z.boolean(),
  disk_bytes: z.number(),
});
export type Resources = z.infer<typeof resources>;

export const MODEL_STATES = [
  "pending_approval",
  "deploying",
  "starting",
  "ready",
  "sleeping",
  "waiting_for_gpu",
  "failed",
  "removed",
] as const;
export type ModelState = (typeof MODEL_STATES)[number];

export const modelConfig = z
  .object({
    env: z.record(z.string(), z.string()).optional(),
    secret_refs: z.array(z.string()).optional(),
    idle_timeout_s: z.number().optional(),
    max_job_seconds: z.number().optional(),
    limits: z.record(z.string(), z.number()).optional(),
  })
  .catchall(z.unknown());
export type ModelConfig = z.infer<typeof modelConfig>;

export const model = z.object({
  name: z.string(),
  image: z.string(),
  mode: z.enum(["sync", "queue"]),
  source: z.enum(["ghcr", "local"]),
  state: z.string(),
  state_detail: z.string().nullable(),
  current_digest: z.string().nullable(),
  previous_digest: z.string().nullable(),
  pending_digest: z.string().nullable(),
  failed_digest: z.string().nullable(),
  approved_resources: resources.nullable(),
  requested_resources: resources.nullable(),
  config: modelConfig,
  declared_limits: z.record(z.string(), z.number()).default({}),
  limits: z.record(z.string(), z.number()).default({}),
  created_at: z.number().nullable(),
  updated_at: z.number().nullable(),
  last_checked_at: z.number().nullable(),
});
export type Model = z.infer<typeof model>;

export const deployment = z.object({
  digest: z.string(),
  status: z.string(),
  reason: z.string().nullable(),
  started_at: z.number().nullable(),
  finished_at: z.number().nullable(),
});

export const modelDetail = model.extend({
  deployments: z.array(deployment),
  schema: z.unknown().nullable(),
  worker_state: z.string().optional(),
  runtime: z
    .object({
      exists: z.boolean(),
      ready: z.boolean(),
      replicas: z.number(),
      worker_replicas: z.number().nullable().optional(),
      pending_reason: z.string().nullable().optional(),
      restarts: z.number(),
    })
    .nullable(),
});
export type ModelDetail = z.infer<typeof modelDetail>;

export const approval = z.object({
  id: z.number(),
  model: z.string(),
  digest: z.string(),
  status: z.string(),
  requested: resources,
  currently_approved: resources.nullable(),
  decided_by: z.string().nullable(),
  decided_at: z.number().nullable(),
  note: z.string().nullable(),
  created_at: z.number().nullable(),
});
export type Approval = z.infer<typeof approval>;

export const apiKey = z.object({
  id: z.string(),
  name: z.string(),
  allowed_models: z.array(z.string()),
  allow_all: z.boolean(),
  expires_at: z.number().nullable(),
  limits: z.record(z.string(), z.number()).default({}),
  revoked_at: z.number().nullable(),
  created_at: z.number(),
});
export type ApiKey = z.infer<typeof apiKey>;
export const apiKeyCreated = apiKey.extend({ key: z.string() });

export const observer = z.object({
  id: z.number(),
  webhook_url: z.string(),
  event_types: z.array(z.string()),
  connection_errors_count: z.number(),
});
export type Observer = z.infer<typeof observer>;

export const registerResult = z.object({ action: z.string(), model: model });
export const configResult = z.object({ redeploy: z.boolean(), model });
export const approveResult = z.object({ deploy_started: z.boolean(), reason: z.string().nullable(), model });

export const usagePoint = z.object({
  t: z.number(),
  cpu_m: z.number(),
  cpu_m_max: z.number(),
  memory_bytes: z.number(),
  memory_bytes_max: z.number(),
});
export type UsagePoint = z.infer<typeof usagePoint>;

export const modelMetrics = z.object({
  range: z.string(),
  step_seconds: z.number(),
  limits: z.object({ cpu_m: z.number().nullable(), memory_bytes: z.number().nullable() }),
  latest: z.object({ t: z.number(), cpu_m: z.number(), memory_bytes: z.number() }).nullable(),
  points: z.array(usagePoint),
});
export type ModelMetrics = z.infer<typeof modelMetrics>;

export const keyUsage = z.object({
  key: z.string(),
  last_used: z.number().nullable(),
  days: z.array(z.object({ day: z.string(), models: z.record(z.string(), z.object({ requests: z.number(), errors: z.number(), limited: z.number() })) })),
});
export type KeyUsage = z.infer<typeof keyUsage>;
