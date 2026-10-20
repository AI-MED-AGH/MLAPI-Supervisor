import { screen, waitFor } from "@testing-library/react";
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
