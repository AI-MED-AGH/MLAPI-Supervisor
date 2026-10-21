import { Field, Input } from "./ui";
import { describeLimit, type LimitDef, type Limits } from "../lib/limits";

/** One number box per limit. Empty = the default applies; 0 = unlimited. */
export function LimitFields({
  defs,
  values,
  onChange,
  applies,
  declared,
}: {
  defs: LimitDef[];
  values: Record<string, string>;
  onChange: (name: string, value: string) => void;
  /** What applies right now, shown as the placeholder of an empty box. */
  applies?: Limits;
  /** What the image asked for, mentioned in the hint. */
  declared?: Limits;
}) {
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      {defs.map((d) => {
        const asked = declared?.[d.name];
        const hint = [d.hint, asked !== undefined ? `The image asks for ${describeLimit(asked)}.` : null].filter(Boolean).join(" ");
        return (
          <Field key={d.name} label={d.label} hint={hint}>
            {(p) => (
              <Input
                {...p}
                type="number"
                step={1}
                inputMode="numeric"
                value={values[d.name] ?? ""}
                placeholder={describeLimit(applies?.[d.name]).replace(/,/g, "")}
                onChange={(e) => onChange(d.name, e.target.value)}
              />
            )}
          </Field>
        );
      })}
    </div>
  );
}
