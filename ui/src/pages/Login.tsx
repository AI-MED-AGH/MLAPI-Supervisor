import { useState, type FormEvent } from "react";
import { ApiError } from "../api/client";
import { useAuth } from "../auth";
import { Button, Card, Field, Input } from "../components/ui";

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
    <main className="grid min-h-screen place-items-center px-4">
      <div className="w-full max-w-sm space-y-6">
        <div className="text-center">
          <span className="mx-auto grid size-12 place-items-center rounded-xl bg-brand-600 text-lg font-bold text-white">M</span>
          <h1 className="mt-4 text-2xl font-semibold tracking-tight">MLAPI Supervisor</h1>
          <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">Sign in with an admin key.</p>
        </div>
        <Card>
          <form onSubmit={submit} className="space-y-4">
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
            <Button type="submit" variant="primary" busy={busy} disabled={!key.trim()} className="w-full">
              Sign in
            </Button>
          </form>
        </Card>
        <p className="text-center text-xs text-slate-500">
          Keys are configured with <code>ADMIN_API_KEYS</code>. Generate one with <code>python -m app.admin_key</code>.
        </p>
      </div>
    </main>
  );
}
