import { cloneElement, useId } from "react";

import { cn } from "./cn";
import type {
  ButtonHTMLAttributes,
  InputHTMLAttributes,
  ReactElement,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from "react";

/**
 * The small set of primitives every screen is built from.
 *
 * PLAN.md names shadcn/ui, and this is written in its idiom — the same Tailwind class
 * vocabulary, the same `cn` helper, the same prop shapes — without its dependencies.
 * What shadcn actually brings to a set of screens like these is Radix behind the
 * interactive components, and the interactions here are a native `<dialog>`, a native
 * `<select>` and a table. Ten more packages to reach the same behaviour is not a trade
 * worth making at this size, and the classes are compatible, so `shadcn add` later drops
 * in over the top rather than beside it.
 */

// -- buttons ---------------------------------------------------------------------------

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md";
};

const BUTTON_VARIANTS = {
  primary: "bg-accent text-accent-ink hover:opacity-90",
  secondary: "bg-surface-2 text-surface-ink hover:bg-surface-border",
  ghost: "text-surface-muted hover:bg-surface-2 hover:text-surface-ink",
  danger: "bg-danger text-accent-ink hover:opacity-90",
} as const;

export function Button({
  variant = "secondary",
  size = "md",
  className,
  ...props
}: ButtonProps) {
  return (
    <button
      type="button"
      className={cn(
        "inline-flex items-center justify-center gap-1.5 rounded-md font-medium",
        "transition-opacity disabled:pointer-events-none disabled:opacity-50",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
        size === "sm" ? "h-8 px-2.5 text-sm" : "h-9 px-3.5 text-sm",
        BUTTON_VARIANTS[variant],
        className,
      )}
      {...props}
    />
  );
}

// -- form controls ---------------------------------------------------------------------

const FIELD =
  "w-full rounded-md border border-surface-border bg-surface-0 px-3 py-1.5 text-sm " +
  "placeholder:text-surface-muted focus-visible:outline-2 focus-visible:outline-accent";

/**
 * Nothing here is a credential, so no password manager should offer to fill or save it.
 *
 * `autocomplete="off"` alone is widely ignored — the browsers honour it for autofill but
 * the extensions decide for themselves — so this carries each vendor's opt-out as well.
 * Spread before `props` so a field that genuinely wants autofill can say so.
 */
const NO_AUTOFILL = {
  autoComplete: "off",
  "data-1p-ignore": "",
  "data-lpignore": "true",
  "data-bwignore": "",
  "data-protonpass-ignore": "",
  "data-form-type": "other",
} as const;

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cn(FIELD, "h-9", className)} {...NO_AUTOFILL} {...props} />;
}

export function Textarea({
  className,
  ...props
}: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      className={cn(FIELD, "font-mono text-[0.8125rem]", className)}
      {...NO_AUTOFILL}
      {...props}
    />
  );
}

export function Select({ className, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={cn(FIELD, "h-9", className)} {...NO_AUTOFILL} {...props} />;
}

/**
 * A short set of choices, shown all at once rather than hidden behind a dropdown.
 *
 * Radios in a named `<fieldset>`, so the group has a name and arrow keys move through it
 * without a keydown handler; the input itself is only visually hidden, never removed, so
 * what a screen reader announces is a real radio group with a real checked member. The
 * row carries the focus ring on the input's behalf — `has-[:focus-visible]` — because the
 * thing being focused is the thing that is not drawn.
 *
 * For two or three options this is fewer interactions than a `<select>` and shows the
 * whole set at rest; anything longer belongs in `Select`.
 */
export function ChoiceList<T extends string>({
  label,
  name,
  value,
  options,
  onChange,
}: {
  label: string;
  /** Groups the radios in the DOM; must be unique on the page. */
  name: string;
  value: T;
  options: ReadonlyArray<{ value: T; label: string }>;
  onChange: (value: T) => void;
}) {
  return (
    <fieldset>
      <legend className="mb-1 text-xs font-medium tracking-wide text-surface-muted uppercase">
        {label}
      </legend>
      <div className="flex flex-col">
        {options.map((option) => {
          const selected = option.value === value;
          return (
            <label
              key={option.value}
              className={cn(
                "flex cursor-pointer items-center justify-between gap-3 rounded-md px-2 py-1.5 text-sm",
                "has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2",
                "has-[:focus-visible]:outline-accent",
                selected
                  ? "bg-surface-2 font-medium"
                  : "text-surface-muted hover:bg-surface-2 hover:text-surface-ink",
              )}
            >
              <input
                type="radio"
                name={name}
                value={option.value}
                checked={selected}
                onChange={() => onChange(option.value)}
                className="sr-only"
              />
              <span>{option.label}</span>
              <span aria-hidden="true" className={cn("text-accent", !selected && "invisible")}>
                ✓
              </span>
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}

/**
 * A labelled control, with the hint described rather than named.
 *
 * The hint has to be *outside* the accessible name. Nesting it made the name of the bulk
 * textarea "User IDs Anything with IDs in it… 0 found", which changes every keystroke —
 * unusable to announce, and unfindable by name in a test.
 */
export function Field({
  label,
  hint,
  className,
  children,
}: {
  label: string;
  hint?: string;
  /** Sizing, for a field that is one of several in a row. */
  className?: string;
  children: ReactElement<{ id?: string; "aria-describedby"?: string }>;
}) {
  const id = useId();
  const hintId = hint ? `${id}-hint` : undefined;
  return (
    <div className={cn("space-y-1", className)}>
      <label htmlFor={id} className="block text-sm font-medium">
        {label}
      </label>
      {cloneElement(children, { id, "aria-describedby": hintId })}
      {hint ? (
        <span id={hintId} className="block text-xs text-surface-muted">
          {hint}
        </span>
      ) : null}
    </div>
  );
}

// -- containers ------------------------------------------------------------------------

export function Card({
  className,
  label,
  children,
}: {
  className?: string;
  /** Names the section for assistive technology — a `<section>` with a name is a
   *  landmark, and these screens stack several that look alike. */
  label?: string;
  children: ReactNode;
}) {
  return (
    <section
      aria-label={label}
      className={cn(
        "rounded-lg border border-surface-border bg-surface-1 p-4 sm:p-5",
        className,
      )}
    >
      {children}
    </section>
  );
}

export function CardTitle({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <header className="mb-3 flex flex-wrap items-center justify-between gap-3">
      <h2 className="text-base font-semibold">{children}</h2>
      {action}
    </header>
  );
}

/**
 * The strip that decides which rows are below it.
 *
 * A bar of its own rather than controls tucked into the page title, because these two
 * screens are read by narrowing them: the audit log and the job queue are both far
 * longer than anybody scrolls, and the box you type a snowflake into is the first thing
 * on them worth reaching. Labels are drawn rather than left to `aria-label`, which is a
 * name for the control and not a thing anybody can see.
 *
 * `role="search"` makes it a landmark, so it can be jumped to instead of tabbed to.
 */
export function FilterBar({
  label,
  onClear,
  children,
}: {
  label: string;
  /** Passed only when something is actually set: a "Clear" that is always there reads
   *  as a filter that is always on. */
  onClear?: () => void;
  children: ReactNode;
}) {
  return (
    <div
      role="search"
      aria-label={label}
      className={cn(
        "mb-4 flex flex-wrap items-start gap-3 rounded-lg border border-surface-border",
        "bg-surface-1 px-4 py-3",
      )}
    >
      {children}
      {onClear ? (
        // mt-6 clears the label row above each control, so the button sits on the row of
        // controls rather than the row of labels.
        <Button size="sm" variant="ghost" className="ml-auto mt-6" onClick={onClear}>
          Clear filters
        </Button>
      ) : null}
    </div>
  );
}

export function PageTitle({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <header className="mb-5 flex flex-wrap items-center justify-between gap-3">
      <h1 className="text-xl font-semibold tracking-tight">{children}</h1>
      {action}
    </header>
  );
}

// -- feedback --------------------------------------------------------------------------

const TONES = {
  neutral: "bg-surface-2 text-surface-ink",
  ban: "bg-danger/15 text-danger",
  warn: "bg-warn/15 text-warn",
  ok: "bg-ok/15 text-ok",
} as const;

export function Badge({
  tone = "neutral",
  children,
}: {
  tone?: keyof typeof TONES;
  children: ReactNode;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium",
        TONES[tone],
      )}
    >
      {children}
    </span>
  );
}

const BANNER_TONES = {
  ok: "bg-ok/10 text-ok",
  warn: "bg-warn/10 text-warn",
  danger: "bg-danger/10 text-danger",
} as const;

/**
 * A standing statement about the screen, above the content it qualifies.
 *
 * Not an `ErrorNote`: that reports what a button just did, and these are true before
 * anybody touches anything — enforcement is paused here, dry run is on, Timothy cannot
 * ban in this server.
 *
 * The `role` is the caller's, because the two are not interchangeable for anyone using a
 * screen reader. `status` is announced politely and is right for a state the page is
 * simply in; `alert` interrupts, and is for the ones that mean something is broken and
 * nothing is working until it is fixed.
 */
export function Banner({
  tone = "warn",
  role = "status",
  className,
  children,
}: {
  tone?: keyof typeof BANNER_TONES;
  role?: "status" | "alert";
  className?: string;
  children: ReactNode;
}) {
  return (
    <div
      role={role}
      className={cn("rounded-md px-4 py-3 text-sm", BANNER_TONES[tone], className)}
    >
      {children}
    </div>
  );
}

/**
 * Whatever went wrong, in the backend's own words.
 *
 * `role="alert"` because these appear after an action rather than on load — a moderator
 * who just pressed a button and heard nothing needs the refusal announced, not merely
 * rendered.
 */
export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  // Anything that is not an `Error` gets a general message rather than
  // "[object Object]" — the point of this box is that somebody can read it.
  const message =
    error instanceof Error
      ? error.message
      : typeof error === "string"
        ? error
        : "Something went wrong.";
  return (
    <p role="alert" className="rounded-md bg-danger/10 px-3 py-2 text-sm text-danger">
      {message}
    </p>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="py-6 text-center text-sm text-surface-muted">{children}</p>;
}

export function Loading({ what }: { what: string }) {
  return (
    <p className="py-6 text-center text-sm text-surface-muted" aria-live="polite">
      Loading {what}…
    </p>
  );
}

// -- tables ----------------------------------------------------------------------------

export function Table({ head, children }: { head: ReactNode[]; children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-surface-border text-left">
            {head.map((cell, index) => (
              <th
                // The header cells are a fixed literal list per table, so the index is
                // stable by construction.
                key={index}
                className="px-2 py-2 text-xs font-semibold tracking-wide text-surface-muted uppercase"
              >
                {cell}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

export function Row({ children }: { children: ReactNode }) {
  return <tr className="border-b border-surface-border/60 last:border-0">{children}</tr>;
}

export function Cell({
  className,
  colSpan,
  children,
}: {
  className?: string;
  colSpan?: number;
  children: ReactNode;
}) {
  return (
    <td colSpan={colSpan} className={cn("px-2 py-2 align-top", className)}>
      {children}
    </td>
  );
}

// -- confirmation ----------------------------------------------------------------------

/**
 * A blocking confirm for the things that reach Discord.
 *
 * Deleting a pool, unsubscribing with `revert`, bulk-removing listings — each of these
 * bans or unbans real people in guilds this screen is not showing. The dialog exists to
 * put the consequence in words before the button works, which is the same reason
 * `?revert=true` has no slash command.
 */
export function Confirm({
  open,
  title,
  body,
  confirmLabel = "Confirm",
  destructive = true,
  busy = false,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  body: ReactNode;
  confirmLabel?: string;
  destructive?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4">
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="w-full max-w-md rounded-lg border border-surface-border bg-surface-1 p-5 shadow-xl"
      >
        <h2 className="text-base font-semibold">{title}</h2>
        <div className="mt-2 space-y-2 text-sm text-surface-muted">{body}</div>
        <div className="mt-5 flex justify-end gap-2">
          <Button onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
          <Button
            variant={destructive ? "danger" : "primary"}
            onClick={onConfirm}
            disabled={busy}
          >
            {busy ? "Working…" : confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}

// -- formatting ------------------------------------------------------------------------

export function When({ iso }: { iso: string }) {
  const at = new Date(iso);
  return (
    <time dateTime={iso} title={at.toISOString()} className="text-surface-muted">
      {at.toLocaleString()}
    </time>
  );
}

/** A Discord ID. Always monospaced, never wrapped, always selectable in one go. */
export function Snowflake({ id, className }: { id: string; className?: string }) {
  return <span className={cn("snowflake", className)}>{id}</span>;
}

/**
 * A server, by what it is called.
 *
 * The name is a cache the gateway fills, so it can be missing — a server registered
 * before Timothy stored names and not seen since, or one Timothy has left — and the ID
 * is shown alone when it is. Where there is a name the ID stays underneath it, quieter:
 * it is what a person pastes into Discord's search and what every log line says, and
 * two servers may well share a name.
 */
export function GuildName({ id, name }: { id: string; name?: string | null }) {
  if (!name) return <Snowflake id={id} />;
  return (
    <span className="flex flex-col gap-0.5">
      <span>{name}</span>
      <Snowflake id={id} className="text-xs text-surface-muted" />
    </span>
  );
}

/** `user:123…` or `system`, rendered so Timothy's own actions are visibly not a person. */
export function ActorRef({ actor }: { actor: string }) {
  if (actor === "system") return <Badge>Timothy</Badge>;
  return <Snowflake id={actor.replace("user:", "")} />;
}
