import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { AuditLog } from "@/routes/AuditLog";
import { GuildDetail } from "@/routes/GuildDetail";

import { apiUrl, get, mockApi, renderWithQuery, server } from "./harness";

mockApi();

const GUILD = "100000000000000002";

const GUILD_ROW = {
  guild_id: GUILD,
  name: "Neon Atrium",
  joined_at: "2026-01-01T00:00:00Z",
  enforcement_paused: false,
};

const SUBSCRIPTION = {
  guild_id: GUILD,
  pool_id: 1,
  pool_name: "global",
  level: "ban",
  created_by: "system",
  created_at: "2026-01-01T00:00:00Z",
};

function guildScreen(overrides: Partial<Record<string, unknown>> = {}) {
  server.use(
    get(`/guilds/${GUILD}`, (overrides.guild ?? GUILD_ROW) as never),
    get(`/guilds/${GUILD}/subscriptions`, (overrides.subscriptions ?? [SUBSCRIPTION]) as never),
    get(`/guilds/${GUILD}/exceptions`, (overrides.exceptions ?? []) as never),
    get(`/guilds/${GUILD}/notification-channel`, { detail: "no such channel" }, 404),
    get(`/guilds/${GUILD}/enforcement`, (overrides.enforcement ?? []) as never),
    get(`/guilds/${GUILD}/diagnostics`, { detail: "not reported" }, 404),
    get(`/guilds/${GUILD}/diagnostics/failures`, []),
    get("/pools", (overrides.pools ?? []) as never),
  );
  return renderWithQuery(<GuildDetail guildId={GUILD} />);
}

describe("a server's page", () => {
  it("explains what the two subscription levels do", async () => {
    // The distinction between warn and ban is the one thing a server administrator most
    // needs to get right, and phase 5 found guilds that had never had it explained.
    guildScreen();

    const card = await screen.findByRole("region", { name: /Subscriptions/i });
    expect(card).toHaveTextContent(/At ban level, everyone listed on the pool is banned/);
    expect(card).toHaveTextContent(/At warn level nobody is banned/);
  });

  it("is titled with the server's name, and still shows its ID", async () => {
    // The name is what an administrator recognises; the ID is what they paste into
    // Discord's search, and two servers may well share a name.
    guildScreen();

    expect(await screen.findByText("Neon Atrium")).toBeInTheDocument();
    expect(screen.getByText(GUILD)).toBeInTheDocument();
  });

  it("falls back to the ID when Timothy has not heard the name yet", async () => {
    // Nothing depends on the name: it is a cache the gateway fills on reconnect, and a
    // server registered before names were stored has none until then.
    guildScreen({ guild: { ...GUILD_ROW, name: null } });

    expect(await screen.findByText(GUILD)).toBeInTheDocument();
  });

  it("says loudly when enforcement is paused", async () => {
    guildScreen({ guild: { ...GUILD_ROW, enforcement_paused: true } });

    expect(await screen.findByRole("status")).toHaveTextContent(
      /Timothy is recording nothing and issuing nothing/,
    );
  });

  it("offers to resume when it is paused, and to pause when it is not", async () => {
    guildScreen({ guild: { ...GUILD_ROW, enforcement_paused: true } });

    expect(
      await screen.findByRole("button", { name: "Resume enforcement" }),
    ).toBeInTheDocument();
  });

  it("warns that unsubscribing with a revert reaches people this screen never showed", async () => {
    const { user } = guildScreen();
    await screen.findByText("global");

    await user.click(screen.getByRole("button", { name: /Unsubscribe & unban/ }));

    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent(/may be a large number of people/i);
    expect(dialog).toHaveTextContent(/Bans this server placed itself are never touched/i);
  });

  it("makes clear that a plain unsubscribe leaves the bans alone", async () => {
    const { user } = guildScreen();
    await screen.findByText("global");

    await user.click(screen.getByRole("button", { name: "Unsubscribe" }));

    expect(screen.getByRole("dialog")).toHaveTextContent(/Everyone already banned stays banned/);
  });

  it("sends revert only when it was asked for", async () => {
    const asked: string[] = [];
    server.use(
      http.delete(apiUrl(`/guilds/${GUILD}/subscriptions/global`), ({ request }) => {
        asked.push(new URL(request.url).searchParams.get("revert") ?? "");
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const { user } = guildScreen();
    await screen.findByText("global");

    await user.click(screen.getByRole("button", { name: /Unsubscribe & unban/ }));
    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Unsubscribe and unban" }),
    );

    await waitFor(() => expect(asked).toEqual(["true"]));
  });

  it("only offers pools the server is not already subscribed to", async () => {
    guildScreen({
      pools: [
        { id: 1, name: "global", description: null, created_by: "system", created_at: "2026-01-01T00:00:00Z" },
        { id: 2, name: "raiders", description: null, created_by: "system", created_at: "2026-01-01T00:00:00Z" },
      ],
    });

    const chooser = await screen.findByLabelText("Subscribe to");
    const options = within(chooser).getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(["Choose a pool…", "raiders"]);
  });

  it("shows an auto-created exception as Timothy's rather than a person's", async () => {
    // ADR 0006 has Timothy create these after an unban. Showing `user:0` was exactly the
    // thing that made the old bot's exception list unreadable.
    guildScreen({
      exceptions: [
        {
          guild_id: GUILD,
          user_id: "300000000000000001",
          reason: "unbanned in this guild",
          created_by: "system",
          created_at: "2026-01-01T00:00:00Z",
        },
      ],
    });

    const card = await screen.findByRole("region", { name: /Exceptions/i });
    expect(await within(card).findByText("Timothy")).toBeInTheDocument();
  });

  it("names each enforcement outcome in words", async () => {
    guildScreen({
      enforcement: [
        {
          guild_id: GUILD,
          user_id: "300000000000000001",
          pool_id: 1,
          status: "skipped_exception",
          reason: "excepted here",
          attempted_at: "2026-08-01T00:00:00Z",
        },
        {
          guild_id: GUILD,
          user_id: "300000000000000002",
          pool_id: 1,
          status: "failed",
          reason: "Timothy cannot ban this member",
          attempted_at: "2026-08-01T00:00:00Z",
        },
      ],
    });

    const card = await screen.findByRole("region", { name: /Enforcement history/i });
    expect(await within(card).findByText("skipped — excepted")).toBeInTheDocument();
    expect(within(card).getByText("failed")).toBeInTheDocument();
    expect(within(card).getByText(/Timothy cannot ban this member/)).toBeInTheDocument();
  });

  it("says that history is only what Timothy did itself", async () => {
    guildScreen();

    expect(
      await screen.findByText(/Bans this server placed are not listed and are never touched/),
    ).toBeInTheDocument();
  });

  it("filters the history by outcome at the backend", async () => {
    const { user } = guildScreen();
    await screen.findByLabelText("Filter by outcome");

    // Registered after the screen is up: `server.use` prepends, so this has to come
    // second to win over the one `guildScreen` installed.
    const asked: Array<string | null> = [];
    server.use(
      http.get(apiUrl(`/guilds/${GUILD}/enforcement`), ({ request }) => {
        asked.push(new URL(request.url).searchParams.get("status"));
        return HttpResponse.json([]);
      }),
    );

    await user.selectOptions(screen.getByLabelText("Filter by outcome"), "failed");

    await waitFor(() => expect(asked.at(-1)).toBe("failed"));
  });
});

describe("the audit log", () => {
  function entry(id: number, actor: string, action: string) {
    return {
      id,
      actor,
      action,
      target: "pool:global",
      detail: { pool_id: 1 },
      at: "2026-08-01T00:00:00Z",
    };
  }

  it("shows Timothy's own actions as Timothy's", async () => {
    server.use(get("/audit-log", [entry(2, "system", "enforcement.ban")]));

    renderWithQuery(<AuditLog />);

    expect(await screen.findByText("Timothy")).toBeInTheDocument();
    expect(screen.getByText("enforcement.ban")).toBeInTheDocument();
  });

  it("pages backwards by id rather than by offset", async () => {
    // The table only grows at one end. An offset would shift under a reader every time
    // somebody added a listing.
    const asked: Array<string | null> = [];
    server.use(
      http.get(apiUrl("/audit-log"), ({ request }) => {
        const query = new URL(request.url).searchParams;
        asked.push(query.get("before_id"));
        return HttpResponse.json(
          query.get("before_id")
            ? [entry(1, "user:1", "pool.create")]
            : Array.from({ length: 50 }, (_, index) =>
                entry(100 - index, "user:1", "listing.create"),
              ),
        );
      }),
    );

    const { user } = renderWithQuery(<AuditLog />);
    await screen.findAllByText("listing.create");

    await user.click(screen.getByRole("button", { name: "Older" }));

    await waitFor(() => expect(asked.at(-1)).toBe("51"));
  });

  it("does not offer another page when the last one was short", async () => {
    server.use(get("/audit-log", [entry(1, "user:1", "pool.create")]));

    renderWithQuery(<AuditLog />);

    await screen.findByText("pool.create");
    expect(screen.getByRole("button", { name: "Older" })).toBeDisabled();
  });

  it("searches and filters at the backend, together", async () => {
    // Both narrow the record, not the fifty rows already fetched — the log has no
    // ceiling on it, and filtering the page would search the page.
    const asked: URLSearchParams[] = [];
    server.use(
      http.get(apiUrl("/audit-log"), ({ request }) => {
        asked.push(new URL(request.url).searchParams);
        return HttpResponse.json([entry(1, "user:1", "pool.create")]);
      }),
    );

    const { user } = renderWithQuery(<AuditLog />);
    await screen.findByText("pool.create");

    await user.selectOptions(screen.getByLabelText("Action"), "enforcement.ban");
    await user.type(screen.getByLabelText("Search"), "3000000");

    await waitFor(() => {
      expect(asked.at(-1)?.get("q")).toBe("3000000");
      expect(asked.at(-1)?.get("action")).toBe("enforcement.ban");
    });
  });

  it("starts the paging over when the search changes", async () => {
    // A cursor from the unfiltered log points into a sequence the filtered log does not
    // have, so the reader would land in the middle of results they never saw the start
    // of.
    const asked: URLSearchParams[] = [];
    server.use(
      http.get(apiUrl("/audit-log"), ({ request }) => {
        asked.push(new URL(request.url).searchParams);
        return HttpResponse.json(
          Array.from({ length: 50 }, (_, index) =>
            entry(100 - index, "user:1", "listing.create"),
          ),
        );
      }),
    );

    const { user } = renderWithQuery(<AuditLog />);
    await screen.findAllByText("listing.create");

    await user.click(screen.getByRole("button", { name: "Older" }));
    await waitFor(() => expect(asked.at(-1)?.get("before_id")).toBe("51"));

    await user.type(screen.getByLabelText("Search"), "spam");

    await waitFor(() => expect(asked.at(-1)?.get("before_id")).toBeNull());
  });

  it("says the log is empty differently from a search that found nothing", async () => {
    server.use(get("/audit-log", []));

    const { user } = renderWithQuery(<AuditLog />);

    expect(await screen.findByText("Nothing recorded.")).toBeInTheDocument();

    await user.type(screen.getByLabelText("Search"), "nobody");

    expect(await screen.findByText("Nothing matches those filters.")).toBeInTheDocument();
  });
});
