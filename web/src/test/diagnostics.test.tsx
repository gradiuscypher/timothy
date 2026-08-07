import { screen, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { GuildDetail } from "@/routes/GuildDetail";

import { apiUrl, get, mockApi, post, renderWithQuery, server } from "./harness";

/**
 * What a server's administrators are told about their own configuration.
 *
 * Every case here is one where showing the *plausible* thing would be worse than showing
 * nothing: an all-clear for a server nobody has checked, a member count of zero for roles
 * nobody could count, and a role sitting level with Timothy rendered as reachable.
 */

mockApi();

const GUILD = "100000000000000002";
const OUTRANKED_USER = "300000000000000001";

const GUILD_ROW = {
  guild_id: GUILD,
  name: "Neon Atrium",
  joined_at: "2026-01-01T00:00:00Z",
  enforcement_paused: false,
};

const ADMIN_ROLE = {
  role_id: "600000000000000002",
  name: "admin",
  position: 9,
  member_count: 3,
  managed: false,
};

const MODERATOR_ROLE = {
  role_id: "600000000000000001",
  name: "moderator",
  position: 5,
  member_count: 12,
  managed: false,
};

const DIAGNOSTICS = {
  guild_id: GUILD,
  observed_at: "2026-08-07T00:00:00Z",
  stale: false,
  can_ban: true,
  is_administrator: false,
  top_role_position: 5,
  top_role_name: "Timothy",
  member_counts_complete: true,
  unbannable_roles: [ADMIN_ROLE, MODERATOR_ROLE],
  unbannable_members: 15,
};

const FAILURE = {
  user_id: OUTRANKED_USER,
  pool_id: 1,
  pool_name: "global",
  reason: "403 Forbidden (error code: 50013): Missing Permissions",
  attempted_at: "2026-08-06T00:00:00Z",
};

function guildScreen(
  overrides: {
    diagnostics?: Record<string, unknown>;
    diagnosticsStatus?: number;
    failures?: unknown[];
  } = {},
) {
  server.use(
    get(`/guilds/${GUILD}`, GUILD_ROW),
    get(`/guilds/${GUILD}/subscriptions`, []),
    get(`/guilds/${GUILD}/exceptions`, []),
    get(`/guilds/${GUILD}/notification-channel`, { detail: "no such channel" }, 404),
    get(`/guilds/${GUILD}/enforcement`, []),
    get("/pools", []),
    get(
      `/guilds/${GUILD}/diagnostics`,
      (overrides.diagnostics ?? DIAGNOSTICS) as never,
      overrides.diagnosticsStatus ?? 200,
    ),
    get(`/guilds/${GUILD}/diagnostics/failures`, (overrides.failures ?? []) as never),
  );
  return renderWithQuery(<GuildDetail guildId={GUILD} />);
}

/** The unbannable-roles card, once its query has actually answered. */
async function rolesCard(): Promise<HTMLElement> {
  const card = await screen.findByRole("region", { name: /Roles Timothy cannot ban/i });
  await within(card).findByText(/Timothy's own highest role is|outranks every role here/i);
  return card;
}

// -- the banner --------------------------------------------------------------

describe("whether Timothy can ban here at all", () => {
  it("raises the alarm when the ban permission has not been granted", async () => {
    // The failure this whole screen exists for. Without it the only evidence is `failed`
    // outcomes accumulating on a page belonging to whoever runs the deployment, not to
    // the administrator who can fix it in thirty seconds.
    guildScreen({ diagnostics: { ...DIAGNOSTICS, can_ban: false } });

    const alarm = await screen.findByRole("alert");
    expect(alarm).toHaveTextContent(/cannot ban anyone in this server/i);
    expect(alarm).toHaveTextContent(/Ban Members permission/i);
  });

  it("says nothing when Timothy can ban", async () => {
    guildScreen();

    await screen.findByRole("region", { name: /Roles Timothy cannot ban/i });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("does not report an all-clear for a server nobody has checked", async () => {
    // A 404 means the bot has never looked. Rendering that the same as "can ban" would
    // be an all-clear nobody measured, which is the one answer worse than no answer.
    guildScreen({ diagnostics: { detail: "not reported" }, diagnosticsStatus: 404 });

    expect(
      await screen.findByText(/has not checked this server's setup yet/i),
    ).toBeInTheDocument();
  });

  it("warns when the snapshot has gone stale", async () => {
    guildScreen({ diagnostics: { ...DIAGNOSTICS, stale: true } });

    expect(await screen.findByText(/last checked this server's setup/i)).toBeInTheDocument();
  });
});

// -- roles out of reach ------------------------------------------------------

describe("the roles Timothy cannot ban", () => {
  it("lists a role sitting level with Timothy, not only the ones above it", async () => {
    // Discord's hierarchy is a strict inequality. Its own settings screen shows the two
    // level with each other and gives no hint that level means out of reach.
    guildScreen();

    const card = await rolesCard();
    expect(within(card).getByText("moderator")).toBeInTheDocument();
    expect(within(card).getByText("admin")).toBeInTheDocument();
    expect(card).toHaveTextContent(/at or above/i);
  });

  it("reports the ceiling as a ceiling rather than a count", async () => {
    // Anyone holding two of these roles is counted twice, and saying so is cheaper than
    // shipping the member lists to the backend to deduplicate them.
    guildScreen();

    expect(await rolesCard()).toHaveTextContent(/Up to 15 people are out of reach/);
  });

  it("leaves the counts blank rather than showing zero when nobody could count", async () => {
    // A zero reads as "nobody is affected", which is exactly the wrong thing to say about
    // a blind spot whose size is unknown.
    guildScreen({
      diagnostics: {
        ...DIAGNOSTICS,
        member_counts_complete: false,
        unbannable_members: null,
        unbannable_roles: [{ ...ADMIN_ROLE, member_count: null }],
      },
    });

    const card = await rolesCard();
    expect(card).toHaveTextContent(/could not count/i);
    expect(card).not.toHaveTextContent(/Up to 0/);
  });

  it("says plainly when Timothy outranks everything", async () => {
    guildScreen({
      diagnostics: { ...DIAGNOSTICS, unbannable_roles: [], unbannable_members: 0 },
    });

    expect(await screen.findByText(/outranks every role here/i)).toBeInTheDocument();
  });

  it("asks the backend for a re-check and says it has been asked for", async () => {
    // The button cannot reach the bot; it records a request the bot collects. So the
    // copy has to promise a re-check rather than a result.
    server.use(post(`/guilds/${GUILD}/diagnostics/refresh`, { guild_id: GUILD, requested: true }, 202));
    const { user } = guildScreen();

    const card = await rolesCard();
    await user.click(within(card).getByRole("button", { name: /Check again/i }));

    expect(await screen.findByText(/Re-check requested/i)).toBeInTheDocument();
  });
});

// -- failed bans -------------------------------------------------------------

describe("bans that failed", () => {
  it("does not ask Discord anything until a row is opened", async () => {
    // The list is free — it is the stored outcomes. The explanation costs a live member
    // lookup, so it waits to be asked for.
    let asked = 0;
    server.use(
      http.get(apiUrl(`/guilds/${GUILD}/diagnostics/failures/${OUTRANKED_USER}`), () => {
        asked += 1;
        return HttpResponse.json({});
      }),
    );
    guildScreen({ failures: [FAILURE] });

    const card = await screen.findByRole("region", { name: /Failed bans/i });
    await within(card).findByRole("button", { name: /Why\?/i });
    expect(asked).toBe(0);
  });

  it("names the roles in the way, and where Timothy sits against them", async () => {
    server.use(
      get(`/guilds/${GUILD}/diagnostics/failures/${OUTRANKED_USER}`, {
        user_id: OUTRANKED_USER,
        blocker: "outranked",
        blocking_roles: [ADMIN_ROLE],
        timothy_top_role_position: 5,
        timothy_top_role_name: "Timothy",
        detail: "403 Forbidden (error code: 50013): Missing Permissions",
      }),
    );
    const { user } = guildScreen({ failures: [FAILURE] });

    const card = await screen.findByRole("region", { name: /Failed bans/i });
    await user.click(await within(card).findByRole("button", { name: /Why\?/i }));

    expect(await within(card).findByText(/holds a role at or above/i)).toBeInTheDocument();
    expect(card).toHaveTextContent(/admin.*position 9/i);
    expect(card).toHaveTextContent(/Timothy sits at position 5/i);
  });

  it("shows Discord's own words when nothing else explains it", async () => {
    server.use(
      get(`/guilds/${GUILD}/diagnostics/failures/${OUTRANKED_USER}`, {
        user_id: OUTRANKED_USER,
        blocker: "unknown",
        blocking_roles: [],
        timothy_top_role_position: 5,
        timothy_top_role_name: "Timothy",
        detail: "500 Internal Server Error",
      }),
    );
    const { user } = guildScreen({ failures: [FAILURE] });

    const card = await screen.findByRole("region", { name: /Failed bans/i });
    await user.click(await within(card).findByRole("button", { name: /Why\?/i }));

    expect(await within(card).findByText("500 Internal Server Error")).toBeInTheDocument();
  });

  it("says plainly when there is nothing to explain", async () => {
    guildScreen();

    const card = await screen.findByRole("region", { name: /Failed bans/i });
    await within(card).findByText(/Nothing Timothy tried to ban here has failed/i);
  });
});
