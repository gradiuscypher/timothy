import { Link, Outlet, useRouterState } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";

import { LOGIN_URL, type SignedIn } from "@/api/client";
import { useLogout, useSignedIn } from "@/api/hooks";
import { cn } from "@/components/cn";
import { ContextNav } from "@/components/ContextNav";
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
import type { Dispatch, FocusEvent, SetStateAction } from "react";

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
  const layout = useLayout();

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
      <TopBar me={session.data} layout={layout} />
      <main className={cn(CONTAINERS[layout], "grow py-6", layout === "rail" && "flex gap-6")}>
        {layout === "rail" ? <ContextNav /> : null}
        {/* `min-w-0` because a flex item defaults to refusing to shrink below its
            content, and this one contains tables of unbreakable user IDs. */}
        <div className={cn("min-w-0", layout === "rail" ? "mx-auto w-full max-w-6xl" : "w-full")}>
          <Outlet />
        </div>
      </main>
    </div>
  );
}

/**
 * How wide the page under this route is allowed to be.
 *
 * Three shapes, because the screens want three different things from the horizontal
 * space and one measure cannot serve all of them:
 *
 * - `default` — a column at a readable measure, centred. Forms and short lists.
 * - `rail` — the same column, unchanged, with `ContextNav` in the margin beside it. The
 *   page is not narrowed to make room; the rail only appears where there is room going
 *   spare. Detail screens, where the next thing you want is usually a sibling.
 * - `wide` — everything there is. The audit log and the job queue are five and six
 *   columns of timestamps, IDs, JSON payloads and error text, and every pixel taken off
 *   them is a wrapped line or a horizontal scrollbar.
 *
 * Each route says which it wants in `router.tsx`, next to its path, rather than this
 * matching on pathnames it would then have to be kept in step with.
 */
export type Layout = "default" | "rail" | "wide";

const CONTAINERS: Record<Layout, string> = {
  default: "mx-auto w-full max-w-6xl px-4 sm:px-6",
  // Wide enough for a page and a rail, capped so the header does not stretch to the far
  // corners of a very large display while the page it belongs to stays centred.
  rail: "mx-auto w-full max-w-[100rem] px-4 sm:px-6",
  wide: "w-full px-4 sm:px-6",
};

function useLayout(): Layout {
  return useRouterState({
    // The deepest match is the route actually being rendered; the root is the shell.
    select: (state) => state.matches.at(-1)?.staticData.layout ?? "default",
  });
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
 * The three ways out of a popover, shared by the two of them in this bar.
 *
 * A popover has to be dismissible by something other than the button that opened it, or
 * it is a trap for anybody who opened it by accident: Escape, a press anywhere else, and
 * tabbing out of the panel all close it. `pointerdown` rather than `click` so a press that
 * starts outside closes immediately instead of waiting to see where it ends.
 *
 * Returns what the wrapping element needs: the ref the outside-press test is made
 * against, and the blur handler for the tab-out case.
 */
function useDismiss(open: boolean, setOpen: Dispatch<SetStateAction<boolean>>) {
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
  }, [open, setOpen]);

  const onBlur = (event: FocusEvent<HTMLDivElement>) => {
    // `relatedTarget` is what focus went *to*, and it is null far more often than it
    // looks. Pressing the pointer on anything unfocusable — which is what every one of
    // the theme choices is, a label around a visually hidden radio — takes focus off the
    // button and gives it to nobody, and that arrives here as a blur to null before the
    // click has reached the radio. Closing on it unmounted the panel mid-press, so the
    // theme could not be changed with a mouse at all.
    //
    // The case this handler is actually for is tabbing out of the panel, and that always
    // names where focus landed. A blur to nowhere is left to the pointerdown listener
    // above, which can see where the press was and keeps the panel open when it was
    // inside.
    if (event.relatedTarget && !event.currentTarget.contains(event.relatedTarget)) {
      setOpen(false);
    }
  };

  return { container, onBlur };
}

/**
 * The settings menu.
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
  const { container, onBlur } = useDismiss(open, setOpen);

  return (
    <div ref={container} className="relative" onBlur={onBlur}>
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

/** One entry in the top bar, as a link or as the button that opens a menu. */
const NAV_ITEM = "rounded-md px-2.5 py-1.5 text-sm";
const NAV_ACTIVE = "bg-surface-2 font-medium";
const NAV_IDLE = "text-surface-muted hover:bg-surface-2 hover:text-surface-ink";

/**
 * Operations, and the pages under it.
 *
 * The job queue was a card at the bottom of the overview, which is the wrong place for
 * it twice over: it is filtered and paged, so it is somewhere you *stay*, and it is the
 * widest table in the app underneath a screen that is a grid of small tiles. It is its
 * own page now, and this is what makes it reachable without a second top-level entry —
 * the overview is still what "Operations" means, and Jobs sits under it.
 *
 * A button rather than a link that also opens on hover: hover menus cannot be reached
 * from a keyboard or a touchscreen without a second mechanism, and the second mechanism
 * is this one anyway.
 *
 * What drops down is a group of ordinary links, not a `role="menu"` — that role promises
 * arrow-key navigation and a roving tabstop, and two links you can Tab through do not
 * need either. Same shape as `SettingsMenu`, which is the other thing in this bar that
 * opens.
 */
function OpsMenu({ path }: { path: string }) {
  const [open, setOpen] = useState(false);
  const { container, onBlur } = useDismiss(open, setOpen);

  const items = [
    { to: "/ops", label: "Overview" },
    { to: "/ops/jobs", label: "Jobs" },
    // Every server's settings, which is not what "Your servers" in the bar above means:
    // that one is the servers this person administers, and the operator administers
    // none of them.
    { to: "/ops/guilds", label: "Servers" },
  ] as const;

  return (
    <div ref={container} className="relative" onBlur={onBlur}>
      <button
        type="button"
        aria-haspopup="true"
        aria-expanded={open}
        onClick={() => setOpen((was) => !was)}
        className={cn(
          NAV_ITEM,
          "inline-flex items-center gap-1",
          path.startsWith("/ops") ? NAV_ACTIVE : NAV_IDLE,
        )}
      >
        Operations
        <CaretIcon />
      </button>
      {open ? (
        <div
          role="group"
          aria-label="Operations"
          className={cn(
            "absolute left-0 z-40 mt-1 w-40 rounded-lg border border-surface-border",
            "bg-surface-1 p-1 shadow-lg",
          )}
        >
          {items.map((item) => {
            const current = path === item.to;
            return (
              <Link
                key={item.to}
                to={item.to}
                aria-current={current ? "page" : undefined}
                onClick={() => setOpen(false)}
                className={cn("block", NAV_ITEM, current ? NAV_ACTIVE : NAV_IDLE)}
              >
                {item.label}
              </Link>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

function CaretIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      aria-hidden="true"
      className="h-3 w-3"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="m6 9 6 6 6-6" />
    </svg>
  );
}

function TopBar({ me, layout }: { me: SignedIn; layout: Layout }) {
  const logout = useLogout();
  const path = useRouterState({ select: (state) => state.location.pathname });

  const links = [
    { to: "/", label: "Home", exact: true },
    // Pools is readable by anyone signed in (`READ_POOLS` is `ANY_GUILD_MEMBER`); only
    // editing is restricted to pool managers, which the pages themselves enforce.
    { to: "/pools", label: "Pools", exact: false },
    { to: "/guilds", label: "Servers", exact: false },
    { to: "/users", label: "Look up a user", exact: false },
    ...(me.manages_pools ? [{ to: "/audit", label: "Audit log", exact: false }] : []),
  ];

  return (
    <header className="border-b border-surface-border bg-surface-1">
      {/* The bar is measured with the page beneath it, so a wide page does not sit under
          a navigation stopping short of it. */}
      <div className={cn(CONTAINERS[layout], "flex flex-wrap items-center gap-x-1 gap-y-2 py-2")}>
        <span className="mr-3 font-semibold tracking-tight">Timothy</span>
        <nav className="flex flex-wrap items-center gap-1">
          {links.map((link) => {
            const active = link.exact ? path === link.to : path.startsWith(link.to);
            return (
              <Link
                key={link.to}
                to={link.to}
                className={cn(NAV_ITEM, active ? NAV_ACTIVE : NAV_IDLE)}
              >
                {link.label}
              </Link>
            );
          })}
          {/* Drawn from `is_owner`, not `manages_pools`: running the deployment and
              owning the pools are different jobs, and the operations views belong to the
              first (ADR 0011). */}
          {me.is_owner ? <OpsMenu path={path} /> : null}
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
