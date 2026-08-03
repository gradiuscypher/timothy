import { Link, Outlet, useRouterState } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";

import { LOGIN_URL, type SignedIn } from "@/api/client";
import { useLogout, useSignedIn } from "@/api/hooks";
import { cn } from "@/components/cn";
import {
  FAMILIES,
  FAMILY_LABELS,
  MODES,
  MODE_LABELS,
  useTheme,
  type Family,
  type Mode,
} from "@/components/theme";
import { Button, ChoiceList, ErrorNote, Loading } from "@/components/ui";

/**
 * The frame every screen is drawn in, and the gate in front of it.
 *
 * Not signed in means the login page rather than a redirect: there is nothing behind it
 * to redirect *to* until the session exists, and a browser that has never logged in
 * reaching a 401 is the ordinary first visit rather than an error.
 *
 * The navigation is drawn from `manages_pools` and `is_owner`, both of which are hints
 * (`/auth/me` says so). Hiding a link the caller cannot use is a courtesy; every route
 * behind it resolves the permission again for itself, so a stale hint costs a 403 and
 * never an escalation.
 */
export function Shell() {
  const session = useSignedIn();

  if (session.isPending) return <Loading what="your session" />;
  if (session.isError) {
    return (
      <main className="mx-auto max-w-lg p-8">
        <ErrorNote error={session.error} />
      </main>
    );
  }
  if (!session.data) return <SignIn />;

  return (
    <div className="flex min-h-full flex-col">
      <TopBar me={session.data} />
      <main className="mx-auto w-full max-w-6xl grow px-4 py-6 sm:px-6">
        <Outlet />
      </main>
    </div>
  );
}

/**
 * Two ways a login ends up back here, and they are not the same problem:
 *
 * - `failed` — Discord refused the exchange. Trying again is the right advice.
 * - `denied` — the login worked and the person is not in the management server. Trying
 *   again does exactly the same thing, so the message has to say what would change it.
 */
const LOGIN_ERRORS: Record<string, string> = {
  failed: "Discord refused that login. Please try again.",
  denied:
    "Signing in is limited to members of the Timothy management server. " +
    "Ask whoever runs it for an invite, then try again.",
};

function SignIn() {
  // `/auth/callback` sends a refused login here rather than showing JSON in the address
  // bar, and this is the half that reads it.
  const reason = new URLSearchParams(window.location.search).get("login");
  const message = reason ? LOGIN_ERRORS[reason] : undefined;

  return (
    <main className="mx-auto grid min-h-full max-w-md place-items-center p-8">
      <div className="w-full space-y-4 text-center">
        <h1 className="text-2xl font-semibold tracking-tight">Timothy</h1>
        <p className="text-sm text-surface-muted">
          Shared moderation for Discord. Sign in to manage the pools you own and the
          servers you administer.
        </p>
        {message ? <ErrorNote error={new Error(message)} /> : null}
        <a
          href={LOGIN_URL}
          className="inline-flex h-10 items-center justify-center rounded-md bg-accent px-5 text-sm font-medium text-accent-ink hover:opacity-90"
        >
          Sign in with Discord
        </a>
        <p className="text-xs text-surface-muted">
          Timothy asks for your identity and the list of servers you are in. Signing in
          needs membership of the management server; what you may do afterwards is decided
          by the permissions you already hold.
        </p>
      </div>
    </main>
  );
}

/**
 * Both theme choices, laid out rather than folded into dropdowns.
 *
 * Still two groups and not one list of every combination, because the two choices are
 * independent — see `theme.ts`. What changed is the control: five options between them is
 * short enough to show whole, and a list you read once beats two dropdowns you open,
 * scan and close. They are radios, so the semantics a `<select>` gave for free are still
 * there; `ChoiceList` says how.
 *
 * The theme itself is not read here. `useTheme` is what applies the stored choice to the
 * document, so it has to run whether or not this panel is on screen — it lives in
 * `SettingsMenu`, which the top bar always renders, and hands the answer down.
 */
function ThemeChoices({
  family,
  mode,
  setFamily,
  setMode,
}: ReturnType<typeof useTheme>) {
  return (
    <div className="space-y-3">
      <ChoiceList
        label="Theme"
        name="theme-family"
        value={family}
        onChange={(next: Family) => setFamily(next)}
        options={FAMILIES.map((option) => ({ value: option, label: FAMILY_LABELS[option] }))}
      />
      <ChoiceList
        label="Light or dark"
        name="theme-mode"
        value={mode}
        onChange={(next: Mode) => setMode(next)}
        options={MODES.map((option) => ({ value: option, label: MODE_LABELS[option] }))}
      />
    </div>
  );
}

/**
 * The settings menu, and the three ways out of it.
 *
 * A popover has to be dismissible by something other than the button that opened it, or
 * it is a trap for anybody who opened it by accident: Escape, a click anywhere else, and
 * focus leaving the panel all close it. `pointerdown` rather than `click` so a press that
 * starts outside closes immediately instead of waiting to see where it ends.
 *
 * Nothing here is destructive and nothing is asynchronous, so the panel stays a plain
 * region rather than a modal — the page behind it is still readable, and the theme
 * changing underneath the open menu is the point.
 *
 * This lives in the top bar, which the shell only renders behind a session. Somebody
 * signed out sees the login screen in whatever theme they last chose but cannot change it
 * there — a control on a page you visit once, to change something you cannot yet see the
 * effect of, is not worth the second copy.
 */
function SettingsMenu() {
  const theme = useTheme();
  const [open, setOpen] = useState(false);
  const container = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!container.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <div
      ref={container}
      className="relative"
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
      }}
    >
      <Button
        size="sm"
        variant="ghost"
        aria-label="Settings"
        aria-expanded={open}
        aria-haspopup="true"
        onClick={() => setOpen((was) => !was)}
      >
        <GearIcon />
      </Button>
      {open ? (
        <div
          role="group"
          aria-label="Settings"
          className={cn(
            "absolute right-0 z-40 mt-1 w-52 rounded-lg border border-surface-border",
            "bg-surface-1 p-3 shadow-lg",
          )}
        >
          <ThemeChoices {...theme} />
        </div>
      ) : null}
    </div>
  );
}

function GearIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      aria-hidden="true"
      className="h-4 w-4"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1.08-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  );
}

function TopBar({ me }: { me: SignedIn }) {
  const logout = useLogout();
  const path = useRouterState({ select: (state) => state.location.pathname });

  const links = [
    { to: "/", label: "Home", exact: true },
    ...(me.manages_pools ? [{ to: "/pools", label: "Pools", exact: false }] : []),
    { to: "/guilds", label: "Servers", exact: false },
    { to: "/users", label: "Look up a user", exact: false },
    ...(me.manages_pools ? [{ to: "/audit", label: "Audit log", exact: false }] : []),
    // Drawn from `is_owner`, not `manages_pools`: running the deployment and owning the
    // pools are different jobs, and the operations view belongs to the first (ADR 0011).
    ...(me.is_owner ? [{ to: "/ops", label: "Operations", exact: false }] : []),
  ];

  return (
    <header className="border-b border-surface-border bg-surface-1">
      <div className="mx-auto flex w-full max-w-6xl flex-wrap items-center gap-x-1 gap-y-2 px-4 py-2 sm:px-6">
        <span className="mr-3 font-semibold tracking-tight">Timothy</span>
        <nav className="flex flex-wrap items-center gap-1">
          {links.map((link) => {
            const active = link.exact ? path === link.to : path.startsWith(link.to);
            return (
              <Link
                key={link.to}
                to={link.to}
                className={cn(
                  "rounded-md px-2.5 py-1.5 text-sm",
                  active
                    ? "bg-surface-2 font-medium"
                    : "text-surface-muted hover:bg-surface-2 hover:text-surface-ink",
                )}
              >
                {link.label}
              </Link>
            );
          })}
        </nav>
        <div className="ml-auto flex items-center gap-2 text-sm">
          <span className="text-surface-muted">{me.username ?? me.actor}</span>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              logout.mutate(undefined, {
                // The session is gone server-side; reloading is the shortest way to be
                // sure nothing rendered from it is still on screen.
                onSuccess: () => window.location.assign("/"),
              });
            }}
          >
            Sign out
          </Button>
          <SettingsMenu />
        </div>
      </div>
    </header>
  );
}
