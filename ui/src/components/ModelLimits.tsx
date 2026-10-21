import { useState, type FormEvent } from "react";
import { usePatchConfig } from "../api/hooks";
import type { ModelDetail } from "../api/schemas";
import { useRun } from "../lib/useRun";
import { limitsToText, MODEL_LIMITS, parseLimits } from "../lib/limits";
import { LimitFields } from "./LimitFields";
import { useToast } from "./toast";
import { Button, Card } from "./ui";

/** How fast the Router lets clients use one model. The image may ask for values (labels); what an admin sets here wins. */
export function ModelLimits({ model }: { model: ModelDetail }) {
  const patch = usePatchConfig();
  const run = useRun();
  const toast = useToast();
  const overrides = model.config.limits ?? {};
  const defs = MODEL_LIMITS.filter((d) => d.name !== "max_pending" || model.mode === "queue");
  const [text, setText] = useState(limitsToText(overrides, defs));
  const [error, setError] = useState<string | null>(null);

  async function send(limits: Record<string, number>, done: string) {
    const result = await run(patch.mutateAsync({ name: model.name, patch: { limits } }), done);
    if (result?.value.redeploy) toast("success", "Queue limit changed: redeploying");
  }

  function save(e: FormEvent) {
    e.preventDefault();
    const parsed = parseLimits(text, defs);
    if ("error" in parsed) return setError(parsed.error);
    setError(null);
    void send(parsed.limits, "Limits saved");
  }

  return (
    <Card title="Rate limits" className="lg:col-span-2">
      <form onSubmit={save} className="space-y-4">
        <p className="text-sm text-mute">
          Values you set here win over what the image asks for. An empty box follows the image, or the platform default when it asks for nothing; the grey number is what applies now. 0 means unlimited.
        </p>
        <LimitFields
          defs={defs}
          values={text}
          onChange={(name, value) => setText((t) => ({ ...t, [name]: value }))}
          applies={model.limits}
          declared={model.declared_limits}
        />
        {error && (
          <p role="alert" className="border-l-2 border-ink pl-3 text-sm font-medium">
            {error}
          </p>
        )}
        <div className="flex flex-wrap justify-end gap-2">
          {Object.keys(overrides).length > 0 && (
            <Button
              onClick={() => {
                setText(limitsToText({}, defs));
                setError(null);
                void send({}, "Following the image's limits again");
              }}
              disabled={patch.isPending}
            >
              Use the image&apos;s values
            </Button>
          )}
          <Button type="submit" variant="primary" busy={patch.isPending}>
            Save limits
          </Button>
        </div>
      </form>
    </Card>
  );
}
