import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "../auth";
import { ToastProvider } from "../components/toast";
import { App } from "../App";

type Handler = (req: { method: string; path: string; body: any; headers: Headers }) => { status?: number; json?: unknown; headers?: Record<string, string> } | undefined;

/** Replaces fetch with a router of canned responses; returns the log of calls. */
export function mockApi(handler: Handler) {
  const calls: { method: string; path: string; body: any; headers: Headers }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init: RequestInit = {}) => {
      const req = {
        method: init.method ?? "GET",
        path: String(input),
        body: init.body ? JSON.parse(String(init.body)) : undefined,
        headers: new Headers(init.headers),
      };
      calls.push(req);
      const res = handler(req) ?? { status: 404, json: { detail: "unmocked " + req.path } };
      const status = res.status ?? 200;
      return new Response(status === 204 ? null : JSON.stringify(res.json ?? {}), { status, headers: res.headers });
    }),
  );
  return calls;
}

export function renderApp(route = "/") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>
        <ToastProvider>
          <AuthProvider>
            <App />
          </AuthProvider>
        </ToastProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

export const RES = { cpu_m: 1000, memory_bytes: 2 * 1024 ** 3, gpu: false, disk_bytes: 10 * 1024 ** 3 };
export const MODEL = {
  name: "xray", image: "ghcr.io/org/xray", mode: "sync", source: "ghcr", state: "ready", state_detail: null,
  current_digest: "sha256:aaaaaaaaaaaaaaaa", previous_digest: "sha256:bbbbbbbbbbbbbbbb", pending_digest: null, failed_digest: null,
  approved_resources: RES, requested_resources: RES, config: {}, created_at: 1, updated_at: 2, last_checked_at: 3,
};
