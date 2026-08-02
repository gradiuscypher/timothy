import { Link, Outlet, useRouterState } from "@tanstack/react-router";

import { LOGIN_URL, type SignedIn } from "@/api/client";
import { useLogout, useSignedIn } from "@/api/hooks";
import { cn } from "@/components/cn";
import { Button, ErrorNote, Loading } from "@/components/ui";

/**
 * The frame every screen is drawn in, and the gate in front of it.
 *
 * Not signed in means the login page rather than a redirect: there is nothing behind it
 * to redirect *to* until the session exists, and a browser that has never logged in
 * reaching a 401 is the ordinary first visit rather than an error.
 *
 * The navigation is drawn from `manages_pools`, which is a hint (`/auth/me` says so).
 * Hiding a link the caller cannot use is a courtesy; every route behind it resolves the
 * permission again for itself, so a stale hint costs a 403 and never an escalation.
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

function SignIn() {
  // `/auth/callback` sends a failed login here rather than showing JSON in the address
  // bar, and this is the half that reads it.
  const failed = new URLSearchParams(window.location.search).get("login") === "failed";

  return (
    <main className="mx-auto grid min-h-full max-w-md place-items-center p-8">
      <div className="w-full space-y-4 text-center">
        <h1 className="text-2xl font-semibold tracking-tight">Timothy</h1>
        <p className="text-sm text-surface-muted">
          Shared moderation for Discord. Sign in to manage the pools you own and the
          servers you administer.
        </p>
        {failed ? (
          <ErrorNote error={new Error("Discord refused that login. Please try again.")} />
        ) : null}
        <a
          href={LOGIN_URL}
          className="inline-flex h-10 items-center justify-center rounded-md bg-accent px-5 text-sm font-medium text-accent-ink hover:opacity-90"
        >
          Sign in with Discord
        </a>
        <p className="text-xs text-surface-muted">
          Timothy asks for your identity and the list of servers you are in. What you may
          do is decided by the permissions you already hold in them.
        </p>
      </div>
    </main>
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
        </div>
      </div>
    </header>
  );
}
