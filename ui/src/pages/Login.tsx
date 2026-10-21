import { useState, type FormEvent } from "react";
import { ApiError } from "../api/client";
import { useAuth } from "../auth";
import { Button, Field, Input, Logo } from "../components/ui";

export function LoginPage() {
  const { login } = useAuth();
  const [key, setKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(key.trim());
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.status === 401
            ? "That admin key is not valid."
            : err.message
          : "Sign-in failed.",
      );
      setBusy(false);
    }
  }

  return (
    <main className="grid min-h-dvh lg:grid-cols-[1.15fr_1fr]">
      <aside className="relative flex flex-col justify-between overflow-hidden bg-sunken px-8 py-10 sm:px-14 lg:py-14">
        <Logo kind="mark" className="pointer-events-none absolute -right-24 -bottom-32 size-[34rem] opacity-[0.07] max-lg:hidden" />
        <Logo kind="wordmark" className="relative h-24 self-start text-ink max-lg:h-16" />
        <p className="relative mt-16 max-w-sm text-2xl leading-snug font-medium tracking-[-0.02em] max-lg:hidden">
          Deploy models, approve their resources, issue API keys.
        </p>
      </aside>

      <div className="flex items-center px-8 py-14 sm:px-14">
        <div className="page w-full max-w-sm">
          <div>
            <p className="font-mono text-xs text-mute">MLAPI Supervisor</p>
            <h1 className="mt-2 text-5xl font-semibold tracking-[-0.04em]">Sign in</h1>
          </div>
          <form onSubmit={submit} className="mt-10 space-y-5">
            <Field label="Admin key" error={error}>
              {(p) => (
                <Input
                  {...p}
                  type="password"
                  autoComplete="current-password"
                  autoFocus
                  required
                  value={key}
                  onChange={(e) => setKey(e.target.value)}
                />
              )}
            </Field>
            <Button type="submit" variant="primary" busy={busy} disabled={!key.trim()} className="w-full py-2.5">
              Sign in
            </Button>
          </form>
          <p className="mt-8 text-xs leading-relaxed text-mute">
            Keys are configured with <code className="font-mono text-ink">ADMIN_API_KEYS</code>. Generate one with{" "}
            <code className="font-mono text-ink">python -m app.admin_key</code>.
          </p>
        </div>
      </div>
    </main>
  );
}
