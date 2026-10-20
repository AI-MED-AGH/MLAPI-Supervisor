import type { z } from "zod";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public retryAfter?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

let unauthorizedHandler: (() => void) | null = null;
/** Called when an authenticated request comes back 401 (expired or revoked session). */
export function onUnauthorized(handler: (() => void) | null) {
  unauthorizedHandler = handler;
}

/** FastAPI errors are `{detail: string}` or, for validation, `{detail: [{loc, msg}]}`. */
export function describeDetail(detail: unknown, fallback: string): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const parts = detail.map((d) => {
      const loc = Array.isArray(d?.loc) ? d.loc.filter((p: unknown) => p !== "body").join(".") : "";
      return loc ? `${loc}: ${d?.msg}` : String(d?.msg ?? "");
    });
    return parts.filter(Boolean).join("; ") || fallback;
  }
  return fallback;
}

type Options = { body?: unknown; signal?: AbortSignal; skipUnauthorizedHandler?: boolean };

export async function request<S extends z.ZodType>(
  method: string,
  path: string,
  schema: S | null,
  { body, signal, skipUnauthorizedHandler }: Options = {},
): Promise<S extends z.ZodType ? z.infer<S> : void> {
  const headers: Record<string, string> = { "X-Requested-With": "mlapi-admin" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let response: Response;
  try {
    response = await fetch(path, {
      method,
      headers,
      credentials: "same-origin",
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError(0, "Cannot reach the Supervisor. Check your connection.");
  }
  if (!response.ok) {
    let detail: unknown;
    try {
      detail = (await response.json())?.detail;
    } catch {
      /* non-JSON error body */
    }
    if (response.status === 401 && !skipUnauthorizedHandler) unauthorizedHandler?.();
    const retry = Number(response.headers.get("Retry-After"));
    throw new ApiError(
      response.status,
      describeDetail(detail, `${response.status} ${response.statusText || "request failed"}`),
      Number.isFinite(retry) && retry > 0 ? retry : undefined,
    );
  }
  if (response.status === 204 || schema === null) return undefined as never;
  const json = await response.json();
  const parsed = schema.safeParse(json);
  if (!parsed.success) {
    console.error("Unexpected response shape from", path, parsed.error);
    throw new ApiError(response.status, "The Supervisor returned an unexpected response (version mismatch?)");
  }
  return parsed.data as never;
}
