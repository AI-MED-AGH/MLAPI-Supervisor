import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeAll } from "vitest";
import { MODEL, RES, mockApi, renderApp } from "./helpers";

// jsdom has no <dialog> implementation for modals
beforeAll(() => {
  HTMLDialogElement.prototype.showModal ||= function (this: HTMLDialogElement) { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close ||= function (this: HTMLDialogElement) { this.removeAttribute("open"); };
});
afterEach(() => vi.unstubAllGlobals());

describe("login", () => {
  it("shows the login page when not signed in and signs in with the key", async () => {
    let signedIn = false;
    const calls = mockApi(({ method, path }) => {
      if (path === "/v1/auth/me") return signedIn ? { json: { authenticated: true } } : { status: 401, json: { detail: "x" } };
      if (path === "/v1/auth/login") { signedIn = true; return { status: 204 }; }
      if (path === "/v1/models") return { json: [] };
      if (path.startsWith("/v1/approvals")) return { json: [] };
      if (path === "/v1/keys") return { json: [] };
      if (path === "/health") return { json: { status: "ok" } };
      return method === "GET" ? { json: [] } : undefined;
    });
    renderApp();
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Admin key"), "my-secret");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("heading", { name: "Overview" })).toBeInTheDocument();
    const login = calls.find((c) => c.path === "/v1/auth/login")!;
    expect(login.body).toEqual({ key: "my-secret" });
    expect(login.headers.get("X-Requested-With")).toBe("mlapi-admin"); // CSRF header on every call
  });

  it("shows an error for a wrong key", async () => {
    mockApi(({ path }) => {
      if (path === "/v1/auth/me") return { status: 401, json: { detail: "x" } };
      if (path === "/v1/auth/login") return { status: 401, json: { detail: "Invalid admin key" } };
    });
    renderApp();
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Admin key"), "wrong");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("That admin key is not valid.")).toBeInTheDocument();
  });

  it("returns to login when the session expires mid-use", async () => {
    let expired = false;
    mockApi(({ path }) => {
      if (path === "/v1/auth/me") return { json: { authenticated: true } };
      if (path === "/v1/models") return expired ? { status: 401, json: { detail: "x" } } : { json: [MODEL] };
      return { json: [] };
    });
    renderApp("/models");
    expect(await screen.findByText("xray")).toBeInTheDocument();
    expired = true;
    await userEvent.setup().click(screen.getByRole("link", { name: "Overview" }));
    expect(await screen.findByLabelText("Admin key")).toBeInTheDocument();
  });
});

describe("models", () => {
  it("lists models and filters by search", async () => {
    mockApi(({ path }) => {
      if (path === "/v1/auth/me") return { json: { authenticated: true } };
      if (path === "/v1/models") return { json: [MODEL, { ...MODEL, name: "ecg", image: "ghcr.io/org/ecg", state: "sleeping" }] };
      return { json: [] };
    });
    renderApp("/models");
    expect(await screen.findByText("xray")).toBeInTheDocument();
    expect(screen.getByText("ecg")).toBeInTheDocument();
    await userEvent.setup().type(screen.getByLabelText("Search models"), "ecg");
    await waitFor(() => expect(screen.queryByText("xray")).not.toBeInTheDocument());
  });

  it("disables actions that do not apply and requires typing the name to delete", async () => {
    const calls = mockApi(({ method, path }) => {
      if (path === "/v1/auth/me") return { json: { authenticated: true } };
      if (path === "/v1/models/xray" && method === "GET") return { json: { ...MODEL, deployments: [], schema: null, runtime: null } };
      if (path === "/v1/models/xray?keep_cache=true") return { json: { ...MODEL, state: "removed" } };
      return { json: [] };
    });
    renderApp("/models/xray");
    const user = userEvent.setup();
    await screen.findByRole("heading", { name: /xray/ });
    expect(screen.getByRole("button", { name: "Wake" })).toBeDisabled(); // only sleeping models can wake
    expect(screen.getByRole("button", { name: "Sleep" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Delete" }));
    const confirm = await screen.findByRole("button", { name: "Delete model" });
    expect(confirm).toBeDisabled();
    await user.type(screen.getByLabelText(/Type "xray"/), "xray");
    expect(confirm).toBeEnabled();
    await user.click(confirm);
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.path === "/v1/models/xray?keep_cache=true")).toBe(true));
  });
});

describe("approvals", () => {
  const request = {
    id: 7, model: "xray", digest: "sha256:cccccccccccccccc", status: "pending",
    requested: { ...RES, memory_bytes: 4 * 1024 ** 3 }, currently_approved: RES,
    decided_by: null, decided_at: null, note: null, created_at: 5,
  };

  it("highlights what needs approval and sends only changed values", async () => {
    const calls = mockApi(({ method, path }) => {
      if (path === "/v1/auth/me") return { json: { authenticated: true } };
      if (path.startsWith("/v1/approvals?")) return { json: [request] };
      if (path === "/v1/approvals/7/approve" && method === "POST") return { json: { deploy_started: true, reason: null, model: MODEL } };
      return { json: [] };
    });
    renderApp("/approvals");
    const user = userEvent.setup();
    expect(await screen.findByText("needs approval")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Approve…" }));
    const memory = await screen.findByLabelText("Memory");
    expect(memory).toHaveValue("4Gi");
    await user.clear(memory);
    await user.type(memory, "3Gi");
    await user.click(screen.getByRole("button", { name: "Approve and deploy" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/v1/approvals/7/approve")?.body).toEqual({ memory: "3Gi" }));
  });

  it("refuses to approve more than requested", async () => {
    const calls = mockApi(({ path }) => {
      if (path === "/v1/auth/me") return { json: { authenticated: true } };
      if (path.startsWith("/v1/approvals?")) return { json: [request] };
      return { json: [] };
    });
    renderApp("/approvals");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Approve…" }));
    const memory = await screen.findByLabelText("Memory");
    await user.clear(memory);
    await user.type(memory, "8Gi");
    await user.click(screen.getByRole("button", { name: "Approve and deploy" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("at most what the image requested");
    expect(calls.some((c) => c.path.endsWith("/approve"))).toBe(false);
  });

  it("shows a conflict from the Supervisor as a message", async () => {
    mockApi(({ path, method }) => {
      if (path === "/v1/auth/me") return { json: { authenticated: true } };
      if (path.startsWith("/v1/approvals?")) return { json: [request] };
      if (method === "POST") return { status: 409, json: { detail: "model 'xray' is busy (deploying); approve again when it is idle" } };
      return { json: [] };
    });
    renderApp("/approvals");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Approve…" }));
    await user.click(await screen.findByRole("button", { name: "Approve and deploy" }));
    expect(await screen.findByText(/is busy \(deploying\)/)).toBeInTheDocument();
  });
});

describe("api keys", () => {
  it("validates patterns, creates a key and shows the plaintext once", async () => {
    const created = { id: "abc123def456", name: "ci", allowed_models: ["xray"], allow_all: false, expires_at: null, revoked_at: null, created_at: 1, key: "mlapi_abc123def456_SECRET" };
    let keys: unknown[] = [];
    const calls = mockApi(({ method, path }) => {
      if (path === "/v1/auth/me") return { json: { authenticated: true } };
      if (path === "/v1/keys" && method === "GET") return { json: keys };
      if (path === "/v1/keys" && method === "POST") { keys = [{ ...created, key: undefined }]; return { status: 201, json: created }; }
      return { json: [] };
    });
    renderApp("/keys");
    const user = userEvent.setup();
    await user.click((await screen.findAllByRole("button", { name: "Create key" }))[0]);
    await user.type(await screen.findByLabelText("Name"), "ci");
    await user.type(screen.getByLabelText("Allowed models"), "Bad Name!");
    await user.click(screen.getAllByRole("button", { name: "Create key" }).at(-1)!);
    expect(await screen.findByRole("alert")).toHaveTextContent("not a valid model name");
    expect(calls.some((c) => c.method === "POST")).toBe(false);

    await user.clear(screen.getByLabelText("Allowed models"));
    await user.type(screen.getByLabelText("Allowed models"), "xray");
    await user.click(screen.getAllByRole("button", { name: "Create key" }).at(-1)!);
    expect(await screen.findByTestId("new-key")).toHaveTextContent("mlapi_abc123def456_SECRET");
    expect(calls.find((c) => c.method === "POST")?.body).toMatchObject({ name: "ci", allowed_models: ["xray"], allow_all: false });

    await user.click(screen.getByRole("button", { name: "I have saved it" }));
    await waitFor(() => expect(screen.queryByText("mlapi_abc123def456_SECRET")).not.toBeInTheDocument());
    expect(await screen.findByText("abc123def456")).toBeInTheDocument(); // listed, without the secret
  });
});

describe("key limits and usage", () => {
  const KEY = { id: "abc123def456", name: "ci", allowed_models: ["xray"], allow_all: false, expires_at: null, revoked_at: null, created_at: 1, limits: {} };

  function mountKeys(initial: Record<string, unknown>[] = []) {
    let keys = initial;
    const calls = mockApi(({ method, path, body }) => {
      if (path === "/v1/auth/me") return { json: { authenticated: true } };
      if (path === "/v1/keys" && method === "GET") return { json: keys };
      if (path === "/v1/keys" && method === "POST") { const k = { ...KEY, ...body, id: "new000000001" }; keys = [k]; return { status: 201, json: { ...k, key: "mlapi_new000000001_S" } }; }
      if (/^\/v1\/keys\/[a-z0-9]+$/.test(path) && method === "PATCH") { keys = keys.map((k) => ({ ...k, ...body })); return { json: keys[0] }; }
      if (path.startsWith("/v1/keys/abc123def456/usage")) {
        return {
          json: {
            key: "abc123def456", last_used: 1_790_000_000,
            days: [
              { day: "20261021", models: { xray: { requests: 5, errors: 1, limited: 0 } } },
              { day: "20261020", models: { xray: { requests: 3, errors: 0, limited: 2 }, "-": { requests: 1, errors: 0, limited: 1 } } },
            ],
          },
        };
      }
      return { json: [] };
    });
    return calls;
  }

  it("creates a key with the limits typed in, leaving blank ones to the Router default", async () => {
    const calls = mountKeys();
    renderApp("/keys");
    const user = userEvent.setup();
    await user.click((await screen.findAllByRole("button", { name: "Create key" }))[0]);
    await user.type(await screen.findByLabelText("Name"), "ci");
    await user.type(screen.getByLabelText("Allowed models"), "xray");
    await user.type(screen.getByLabelText("Requests per minute"), "30");
    await user.type(screen.getByLabelText("Jobs per day"), "0");
    await user.click(screen.getAllByRole("button", { name: "Create key" }).at(-1)!);
    await screen.findByTestId("new-key");
    expect(calls.find((c) => c.method === "POST")?.body).toMatchObject({ limits: { rpm: 30, jobs_per_day: 0 } });
  });

  it("refuses a bad limit without calling the Supervisor", async () => {
    const calls = mountKeys();
    renderApp("/keys");
    const user = userEvent.setup();
    await user.click((await screen.findAllByRole("button", { name: "Create key" }))[0]);
    await user.type(await screen.findByLabelText("Name"), "ci");
    await user.type(screen.getByLabelText("Allowed models"), "xray");
    await user.type(screen.getByLabelText("Requests per minute"), "-5");
    await user.click(screen.getAllByRole("button", { name: "Create key" }).at(-1)!);
    expect(await screen.findByRole("alert")).toHaveTextContent("Requests per minute");
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("shows each key's limits in the list and edits them", async () => {
    const calls = mountKeys([{ ...KEY, limits: { rpm: 30 } }]);
    renderApp("/keys");
    const user = userEvent.setup();
    expect(await screen.findByText("30 req/min")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Edit ci" }));
    const rpm = await screen.findByLabelText("Requests per minute");
    expect(rpm).toHaveValue(30);
    await user.clear(rpm);
    await user.type(screen.getByLabelText("Job polls per minute"), "600");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toMatchObject({ limits: { poll_rpm: 600 } }));
    expect(calls.find((c) => c.method === "PATCH")?.body.limits).not.toHaveProperty("rpm"); // cleared = back to the default
  });

  it("shows what a key was used for over the last days", async () => {
    const calls = mountKeys([KEY]);
    renderApp("/keys");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Usage of ci" }));
    const dialog = await screen.findByRole("dialog", { name: /Usage of ci/ });
    const xray = (await within(dialog).findByRole("cell", { name: "xray" })).closest("tr")!;
    expect(within(xray).getAllByRole("cell").map((c) => c.textContent)).toEqual(["xray", "8", "1", "2"]);
    expect(within(dialog).getByText(/before a model was identified/i)).toBeInTheDocument(); // the "-" row is explained, not shown raw
    expect(calls.some((c) => c.path === "/v1/keys/abc123def456/usage?days=7")).toBe(true);
    await user.click(within(dialog).getByRole("button", { name: "30 days" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/v1/keys/abc123def456/usage?days=30")).toBe(true));
  });
});

describe("model limits", () => {
  const detail = (over: Record<string, unknown> = {}) => ({ ...MODEL, deployments: [], schema: null, runtime: null, declared_limits: {}, limits: {}, ...over });

  function mount(model: Record<string, unknown>) {
    return mockApi(({ method, path, body }) => {
      if (path === "/v1/auth/me") return { json: { authenticated: true } };
      if (path === "/v1/models/xray" && method === "GET") return { json: model };
      if (path === "/v1/models/xray/config" && method === "PATCH") return { json: { redeploy: !!body.limits?.max_pending, model } };
      return { json: [] };
    });
  }

  it("shows what the image asked for next to what applies, and who set it", async () => {
    mount(detail({ declared_limits: { rpm_per_key: 1000 }, limits: { rpm_per_key: 600 }, config: {} }));
    renderApp("/models/xray");
    const card = (await screen.findByRole("heading", { name: "Rate limits" })).closest("section")!;
    expect(within(card).getByText(/image asks for 1,000/i)).toBeInTheDocument();
    expect(within(card).getByLabelText("Requests per minute, per key")).toHaveAttribute("placeholder", "600"); // what applies now
  });

  it("saves an admin override and can hand control back to the image", async () => {
    const calls = mount(detail({ config: { limits: { rpm_total: 100 } }, limits: { rpm_total: 100 } }));
    renderApp("/models/xray");
    const user = userEvent.setup();
    const total = await screen.findByLabelText("Requests per minute, all keys");
    expect(total).toHaveValue(100);
    await user.type(screen.getByLabelText("Concurrent requests"), "4");
    await user.click(screen.getByRole("button", { name: "Save limits" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ limits: { rpm_total: 100, max_concurrency: 4 } }));

    await user.click(screen.getByRole("button", { name: "Use the image's values" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "PATCH").at(-1)?.body).toEqual({ limits: {} }));
  });

  it("offers the queue limit only for queue-mode models, and says saving it redeploys", async () => {
    mount(detail());
    const first = renderApp("/models/xray");
    await screen.findByLabelText("Concurrent requests");
    expect(screen.queryByLabelText("Queued jobs")).not.toBeInTheDocument();
    first.unmount();
    vi.unstubAllGlobals();
    mount(detail({ mode: "queue" }));
    renderApp("/models/xray");
    expect(await screen.findByLabelText("Queued jobs")).toBeInTheDocument();
    expect(screen.getByText(/redeploys the model/i)).toBeInTheDocument();
  });
});

describe("resource use", () => {
  const GI = 1024 ** 3;
  const now = Math.floor(Date.now() / 1000);
  const point = (ago: number, cpu: number, mem: number) => ({
    t: now - ago, cpu_m: cpu, cpu_m_max: cpu * 1.5, memory_bytes: mem, memory_bytes_max: mem * 1.2,
  });
  const metrics = (range: string, over: Record<string, unknown> = {}) => ({
    range,
    step_seconds: range === "1h" ? 5 : 60,
    limits: { cpu_m: 1000, memory_bytes: 2 * GI },
    latest: { t: now - 3, cpu_m: 500, memory_bytes: GI },
    points: [point(30, 250, GI / 2), point(25, 500, GI), point(20, 750, GI)],
    ...over,
  });

  function mount(over: (range: string) => Record<string, unknown> = () => ({})) {
    return mockApi(({ method, path }) => {
      if (path === "/v1/auth/me") return { json: { authenticated: true } };
      if (path === "/v1/models/xray" && method === "GET") return { json: { ...MODEL, deployments: [], schema: null, runtime: null } };
      const m = /^\/v1\/models\/xray\/metrics\?range=(.+)$/.exec(path);
      if (m) return { json: metrics(m[1], over(m[1])) };
      return { json: [] };
    });
  }

  it("shows what the model uses right now against its limits", async () => {
    mount();
    renderApp("/models/xray");
    expect(await screen.findByText("CPU now")).toBeInTheDocument();
    const cpu = within(screen.getByText("CPU now").parentElement!);
    expect(cpu.getByText("500m")).toBeInTheDocument();
    expect(cpu.getByText("of 1 core")).toBeInTheDocument();
    const memory = within(screen.getByText("Memory now").parentElement!);
    expect(memory.getByText("1 GiB")).toBeInTheDocument();
    expect(memory.getByText("of 2 GiB")).toBeInTheDocument();
  });

  it("loads the selected range and says when a chart has no data", async () => {
    const calls = mount((range) => (range === "30d" ? { points: [] } : {}));
    renderApp("/models/xray");
    const user = userEvent.setup();
    await screen.findByText("CPU now");
    expect(calls.some((c) => c.path === "/v1/models/xray/metrics?range=1h")).toBe(true);
    await user.click(screen.getByRole("button", { name: "30 days" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/v1/models/xray/metrics?range=30d")).toBe(true));
    expect(await screen.findAllByText("No measurements in this range")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "30 days" })).toHaveAttribute("aria-pressed", "true");
  });

  it("reads a point with the keyboard, not only with the pointer", async () => {
    mount();
    renderApp("/models/xray");
    const user = userEvent.setup();
    await screen.findByText("CPU now");
    const [cpuChart] = screen.getAllByRole("img");
    cpuChart.focus();
    await user.keyboard("{ArrowRight}");
    expect(await screen.findByRole("status")).toHaveTextContent("250m");
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("status")).toHaveTextContent("500m");
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("reports a model that is not running instead of showing zeros", async () => {
    mount(() => ({ latest: null, points: [] }));
    renderApp("/models/xray");
    expect(await screen.findByText(/Not running right now/)).toBeInTheDocument();
  });

  it("lists the same values in a table", async () => {
    mount();
    renderApp("/models/xray");
    await screen.findByText("CPU now");
    expect(screen.getByText("Table view")).toBeInTheDocument();
    expect(screen.getAllByRole("row").length).toBeGreaterThanOrEqual(4); // header + 3 points
  });
});
