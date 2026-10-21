import { useEffect, useId, useRef, useState, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode } from "react";
import { ApiError } from "../api/client";
import { stateLabel, stateTone, type Tone } from "../lib/state";

export const cx = (...parts: (string | false | null | undefined)[]) => parts.filter(Boolean).join(" ");

/* ───────── buttons ───────── */
type Variant = "primary" | "secondary" | "danger" | "ghost";
const VARIANTS: Record<Variant, string> = {
  primary: "bg-ink text-canvas hover:bg-ink/85 disabled:opacity-40",
  secondary: "border border-rule text-ink hover:border-ink hover:bg-raised disabled:opacity-40 disabled:hover:border-rule disabled:hover:bg-transparent",
  danger: "hazard border border-ink text-ink hover:bg-ink hover:bg-none hover:text-canvas disabled:opacity-40 disabled:hover:bg-transparent disabled:hover:text-ink",
  ghost: "text-mute hover:bg-raised hover:text-ink disabled:opacity-40",
};

export function Button({
  variant = "secondary",
  busy,
  className,
  children,
  disabled,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; busy?: boolean }) {
  return (
    <button
      type="button"
      {...rest}
      disabled={disabled || busy}
      className={cx(
        "inline-flex items-center justify-center gap-2 rounded-md px-3.5 py-2 text-sm font-medium transition duration-150 ease-out active:translate-y-px active:scale-[0.98]",
        VARIANTS[variant],
        className,
      )}
    >
      {busy && <Spinner className="size-4" />}
      {children}
    </button>
  );
}

export function Spinner({ className = "size-5" }: { className?: string }) {
  return (
    <svg className={cx("animate-spin", className)} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="3" opacity="0.25" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

/* ───────── badges ───────── */
/* Without colour the tone is carried by the outline and the dot: solid = good, pulsing = in progress,
   dashed = needs a person, inverted = failed. The label is always printed as well. */
const TONE_CLASS: Record<Tone, string> = {
  green: "text-ink",
  blue: "text-ink",
  amber: "border border-dashed border-rule text-ink",
  red: "bg-ink text-canvas",
  slate: "bg-sunken text-mute",
};
const DOT_CLASS: Record<Tone, string> = {
  green: "bg-current",
  blue: "animate-beat bg-current",
  amber: "border-[1.5px] border-current",
  red: "rotate-45 rounded-none bg-current",
  slate: "border-[1.5px] border-current opacity-60",
};

export function Badge({ tone = "slate", dot, children }: { tone?: Tone; dot?: boolean; children: ReactNode }) {
  const showDot = dot ?? tone !== "slate";
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1.5 rounded-sm px-1.5 py-0.5 font-mono text-[11px] leading-4 tracking-wide whitespace-nowrap",
        showDot && tone !== "red" && tone !== "amber" && "pl-0",
        TONE_CLASS[tone],
      )}
    >
      {showDot && <span aria-hidden="true" className={cx("size-1.5 shrink-0 rounded-full", DOT_CLASS[tone])} />}
      {children}
    </span>
  );
}

export const StateBadge = ({ state }: { state: string }) => (
  <Badge tone={stateTone(state)} dot>
    {stateLabel(state)}
  </Badge>
);

/* ───────── layout bits ───────── */
export function Logo({ kind = "mark", className }: { kind?: "mark" | "wordmark"; className?: string }) {
  return kind === "mark" ? (
    <span aria-hidden="true" className={cx("logo-mark", className)} />
  ) : (
    <span role="img" aria-label="AI-Med" className={cx("logo-wordmark", className)} />
  );
}

export function Card({ title, actions, children, className }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cx("rounded-lg bg-raised", className)}>
      {(title || actions) && (
        <header className="flex flex-wrap items-center justify-between gap-2 px-5 pt-4 pb-3">
          <h2 className="text-sm font-semibold tracking-tight">{title}</h2>
          {actions}
        </header>
      )}
      <div className={cx("px-5 pb-5", !(title || actions) && "pt-5")}>{children}</div>
    </section>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-10 flex flex-wrap items-end justify-between gap-x-6 gap-y-4">
      <div className="min-w-0">
        <h1 className="truncate text-4xl font-semibold tracking-[-0.035em] sm:text-5xl">{title}</h1>
        {subtitle && <p className="mt-3 max-w-xl text-sm leading-relaxed text-mute">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div role="status" className="space-y-3 py-6">
      <span className="sr-only">{label}</span>
      {["w-full", "w-11/12", "w-3/4", "w-5/6"].map((w, i) => (
        <div key={i} aria-hidden="true" className={cx("h-10 animate-pulse rounded-md bg-raised", w)} style={{ animationDelay: `${i * 120}ms` }} />
      ))}
    </div>
  );
}

export function ErrorState({ error, retry }: { error: unknown; retry?: () => void }) {
  const text = error instanceof ApiError ? error.message : "Something went wrong.";
  return (
    <div role="alert" className="hazard rounded-lg border border-ink p-5 text-sm">
      <p className="font-semibold">Could not load data</p>
      <p className="mt-1 text-mute">{text}</p>
      {retry && (
        <Button className="mt-4" onClick={retry}>
          Try again
        </Button>
      )}
    </div>
  );
}

export function EmptyState({ title, hint, action }: { title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-start gap-3 rounded-lg border border-dashed border-rule px-8 py-14">
      <Logo className="mb-2 size-10 opacity-25" />
      <p className="text-lg font-semibold tracking-tight">{title}</p>
      {hint && <p className="max-w-md text-sm leading-relaxed text-mute">{hint}</p>}
      {action}
    </div>
  );
}

/* ───────── forms ───────── */
export function Field({
  label,
  hint,
  error,
  children,
}: {
  label: string;
  hint?: string;
  error?: string | null;
  children: (props: { id: string; "aria-describedby"?: string; "aria-invalid"?: boolean }) => ReactNode;
}) {
  const id = useId();
  const described = hint || error ? `${id}-d` : undefined;
  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="block text-sm font-medium tracking-tight">
        {label}
      </label>
      {children({ id, "aria-describedby": described, "aria-invalid": error ? true : undefined })}
      {(hint || error) && (
        <p id={described} className={cx("text-xs", error ? "font-medium text-ink" : "text-mute")}>
          {error && <span aria-hidden="true" className="mr-1.5 inline-block size-1.5 rotate-45 bg-ink" />}
          {error || hint}
        </p>
      )}
    </div>
  );
}

export const inputClass =
  "block w-full rounded-md border border-rule bg-sunken px-3 py-2 text-sm transition-colors duration-150 placeholder:text-mute/60 hover:border-ink/50 focus-visible:border-ink focus-visible:outline-offset-0 disabled:opacity-50";

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={cx(inputClass, props.className)} />;
}

export function Checkbox({ label, ...rest }: InputHTMLAttributes<HTMLInputElement> & { label: ReactNode }) {
  return (
    <label className="flex items-center gap-2.5 text-sm">
      <input type="checkbox" {...rest} className="size-4 rounded-sm accent-ink" />
      {label}
    </label>
  );
}

/* ───────── dialogs ───────── */
export function Dialog({
  open,
  onClose,
  title,
  children,
  wide,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (open && !el.open) el.showModal();
    if (!open && el.open) el.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onClose={onClose}
      onClick={(e) => e.target === ref.current && onClose()}
      className={cx(
        "m-auto w-[calc(100%-2rem)] rounded-lg border border-hairline bg-raised p-0 text-ink shadow-2xl shadow-black/40",
        wide ? "max-w-2xl" : "max-w-md",
      )}
    >
      {open && (
        <div className="p-7">
          <h2 id={titleId} className="mb-5 text-2xl font-semibold tracking-[-0.025em]">
            {title}
          </h2>
          {children}
        </div>
      )}
    </dialog>
  );
}

export function ConfirmDialog({
  open,
  onClose,
  onConfirm,
  title,
  body,
  confirmLabel,
  typeToConfirm,
  busy,
  danger = true,
  children,
}: {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  body: ReactNode;
  confirmLabel: string;
  /** Destructive actions require typing this exact text. */
  typeToConfirm?: string;
  busy?: boolean;
  danger?: boolean;
  children?: ReactNode;
}) {
  const [typed, setTyped] = useState("");
  useEffect(() => {
    if (!open) setTyped("");
  }, [open]);
  const ok = !typeToConfirm || typed === typeToConfirm;
  return (
    <Dialog open={open} onClose={onClose} title={title}>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (ok) onConfirm();
        }}
        className="space-y-4"
      >
        <div className="text-sm leading-relaxed text-mute">{body}</div>
        {children}
        {typeToConfirm && (
          <Field label={`Type "${typeToConfirm}" to confirm`}>
            {(p) => <Input {...p} value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" autoFocus />}
          </Field>
        )}
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant={danger ? "danger" : "primary"} disabled={!ok} busy={busy}>
            {confirmLabel}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

/* ───────── misc ───────── */
export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        } catch {
          /* clipboard unavailable (insecure context): the value stays selectable */
        }
      }}
    >
      {copied ? "Copied" : label}
    </Button>
  );
}

export function Mono({ children, title }: { children: ReactNode; title?: string }) {
  return (
    <code title={title} className="rounded-sm bg-sunken px-1.5 py-0.5 font-mono text-xs break-words">
      {children}
    </code>
  );
}

export function Table({ head, children }: { head: ReactNode[]; children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead className="text-xs text-mute">
          <tr className="border-b border-ink/70">
            {head.map((h, i) => (
              <th key={i} scope="col" className="px-4 py-3 font-medium whitespace-nowrap first:pl-0 last:pr-0">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-hairline [&>tr]:transition-colors [&>tr:hover]:bg-raised">{children}</tbody>
      </table>
    </div>
  );
}

export const Td = ({ children, className }: { children?: ReactNode; className?: string }) => (
  <td className={cx("px-4 py-4 align-middle first:pl-0 last:pr-0", className)}>{children}</td>
);
