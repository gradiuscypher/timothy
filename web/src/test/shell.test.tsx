import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MEMBER, OWNER, SIGNED_IN, get, mockApi, renderApp, server } from "./harness";

mockApi();

const GUILD = "100000000000000002";

describe("the shell", () => {
  it("asks an unauthenticated browser to sign in", async () => {
    // A 401 here is the ordinary first visit, not a failure.
    server.use(get("/auth/me", { detail: "no credentials" }, 401));

    renderApp();

    expect(await screen.findByText(/Sign in with Discord/)).toBeInTheDocument();
  });

  it("sends the browser to the backend to log in, never to Discord directly", async () => {
    // The client ID and the state cookie are the backend's business. A link built here
    // would be a second copy of the flow, out of step with the one that validates it.
    server.use(get("/auth/me", { detail: "no credentials" }, 401));

    renderApp();

    const link = await screen.findByRole("link", { name: /Sign in with Discord/ });
    expect(link).toHaveAttribute("href", "/api/auth/login");
  });

  it("says so when Discord refused the login", async () => {
    window.history.replaceState({}, "", "/?login=failed");
    server.use(get("/auth/me", { detail: "no credentials" }, 401));

    renderApp();

    expect(await screen.findByRole("alert")).toHaveTextContent(/Discord refused/);
    window.history.replaceState({}, "", "/");
  });

  it("says what would change it when the login was refused at the door", async () => {
    // A login from outside the management server is not a login to retry (ADR 0013), so
    // it must not get the "please try again" message.
    window.history.replaceState({}, "", "/?login=denied");
    server.use(get("/auth/me", { detail: "no credentials" }, 401));

    renderApp();

    expect(await screen.findByRole("alert")).toHaveTextContent(/management server/);
    window.history.replaceState({}, "", "/");
  });

  it("shows the pool screens to somebody who owns pools", async () => {
    server.use(get("/auth/me", SIGNED_IN), get("/guilds", []), get("/pools", []));

    renderApp();

    expect(await screen.findByRole("link", { name: "Pools" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Audit log" })).toBeInTheDocument();
  });

  it("does not offer the operations view to somebody who merely owns pools", async () => {
    // Running the deployment and owning the pools are different jobs (ADR 0011). This is
    // the one place in the UI where that shows.
    server.use(get("/auth/me", SIGNED_IN), get("/guilds", []), get("/pools", []));

    renderApp();

    await screen.findByRole("link", { name: "Pools" });
    expect(screen.queryByRole("button", { name: /Operations/ })).not.toBeInTheDocument();
  });

  it("offers it to whoever runs the deployment", async () => {
    server.use(get("/auth/me", OWNER), get("/guilds", []), get("/pools", []));

    renderApp();

    expect(await screen.findByRole("button", { name: /Operations/ })).toBeInTheDocument();
    // Pools is readable by anyone signed in; owning the deployment does not add pool
    // management, so the audit log — which does need it — stays off the nav.
    expect(screen.getByRole("link", { name: "Pools" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Audit log" })).not.toBeInTheDocument();
  });

  it("keeps the queue under Operations rather than beside it", async () => {
    // Jobs is an operator's page and belongs to the same permission; a second top-level
    // entry for it would say otherwise.
    server.use(get("/auth/me", OWNER), get("/guilds", []), get("/ops/jobs", []));

    const { user } = renderApp();
    await user.click(await screen.findByRole("button", { name: /Operations/ }));

    const menu = screen.getByRole("group", { name: "Operations" });
    expect(within(menu).getByRole("link", { name: "Overview" })).toBeInTheDocument();

    await user.click(within(menu).getByRole("link", { name: "Jobs" }));

    expect(await screen.findByRole("heading", { name: "Jobs" })).toBeInTheDocument();
  });

  it("closes the menu on Escape", async () => {
    // A popover that only its own button can dismiss is a trap for anyone who opened it
    // by accident.
    server.use(get("/auth/me", OWNER), get("/guilds", []));

    const { user } = renderApp();
    await user.click(await screen.findByRole("button", { name: /Operations/ }));
    await user.keyboard("{Escape}");

    expect(screen.queryByRole("group", { name: "Operations" })).not.toBeInTheDocument();
  });

  it("hides editor-only screens from somebody who does not manage pools", async () => {
    // A courtesy, not a gate — every route behind these resolves the permission again.
    // Pools itself stays on the nav: reading pools and listings only needs membership of
    // the management guild, which signing in already established (ADR 0013).
    server.use(get("/auth/me", MEMBER), get("/guilds", []), get("/pools", []));

    renderApp();

    await waitFor(() => expect(screen.getByText("member")).toBeInTheDocument());
    expect(screen.getByRole("link", { name: "Pools" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Audit log" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Servers" })).toBeInTheDocument();
  });

  it("tells a member with no servers why the list is empty", async () => {
    server.use(get("/auth/me", MEMBER), get("/guilds", []));

    renderApp("/guilds");

    expect(await screen.findByText(/sign out and back in/)).toBeInTheDocument();
  });

  it("puts the other pools and servers beside a server", async () => {
    // The rail is a shortcut, so what matters is that it names the siblings and marks
    // where the reader already is. Whether it is *visible* is a media query — see
    // `--breakpoint-rail` — and no test in jsdom can see one.
    server.use(
      get("/auth/me", SIGNED_IN),
      get("/pools", [
        {
          id: 1,
          name: "global",
          description: null,
          created_by: "system",
          created_at: "2026-01-01T00:00:00Z",
        },
      ]),
      get("/guilds", [
        { guild_id: GUILD, name: "Neon Atrium", joined_at: "2026-01-01T00:00:00Z", enforcement_paused: false },
        { guild_id: "100000000000000009", name: "Somewhere Else", joined_at: "2026-01-01T00:00:00Z", enforcement_paused: false },
      ]),
      get(`/guilds/${GUILD}`, {
        guild_id: GUILD,
        name: "Neon Atrium",
        joined_at: "2026-01-01T00:00:00Z",
        enforcement_paused: false,
      }),
      get(`/guilds/${GUILD}/subscriptions`, []),
      get(`/guilds/${GUILD}/exceptions`, []),
      get(`/guilds/${GUILD}/notification-channel`, { detail: "no such channel" }, 404),
      get(`/guilds/${GUILD}/enforcement`, []),
    );

    renderApp(`/guilds/${GUILD}`);

    const servers = await screen.findByRole("navigation", { name: "Servers" });
    expect(within(servers).getByRole("link", { name: "Somewhere Else" })).toBeInTheDocument();
    expect(within(servers).getByRole("link", { name: "Neon Atrium" })).toHaveAttribute(
      "aria-current",
      "page",
    );

    const pools = screen.getByRole("navigation", { name: "Pools" });
    expect(within(pools).getByRole("link", { name: "global" })).toBeInTheDocument();
  });

  it("shows the pools rail to a member who cannot edit them", async () => {
    // Reading pools only needs membership of the management guild, which signing in
    // already established (ADR 0013), so the rail names them for a plain member too —
    // only the pages themselves withhold the editing controls.
    server.use(
      get("/auth/me", MEMBER),
      get("/pools", [
        {
          id: 1,
          name: "global",
          description: null,
          created_by: "system",
          created_at: "2026-01-01T00:00:00Z",
        },
      ]),
      get("/guilds", [
        {
          guild_id: GUILD,
          name: "Neon Atrium",
          joined_at: "2026-01-01T00:00:00Z",
          enforcement_paused: false,
        },
      ]),
      get(`/guilds/${GUILD}`, {
        guild_id: GUILD,
        name: "Neon Atrium",
        joined_at: "2026-01-01T00:00:00Z",
        enforcement_paused: false,
      }),
      get(`/guilds/${GUILD}/subscriptions`, []),
      get(`/guilds/${GUILD}/exceptions`, []),
      get(`/guilds/${GUILD}/notification-channel`, { detail: "no such channel" }, 404),
      get(`/guilds/${GUILD}/enforcement`, []),
    );

    renderApp(`/guilds/${GUILD}`);

    await screen.findByRole("navigation", { name: "Servers" });
    const pools = await screen.findByRole("navigation", { name: "Pools" });
    expect(within(pools).getByRole("link", { name: "global" })).toBeInTheDocument();
  });
});
