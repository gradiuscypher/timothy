import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { Ops } from "@/routes/Ops";

import { apiUrl, get, mockApi, renderWithQuery, server } from "./harness";

mockApi();

const OVERVIEW = {
  dry_run: false,
  workers_enabled: true,
  enforcement_burst_limit: 25,
  sweep_interval_seconds: 604800,
  management_guild_id: "100000000000000001",
  login_configured: true,
  counts: {
    guilds: 123,
    guilds_paused: 1,
    pools: 5,
    listings: 3076,
    subscriptions: 130,
    exceptions: 12,
    notification_channels: 40,
  },
  queue: {
    pending: 12,
    running: 1,
    done: 900,
    failed: 0,
    sweep_outstanding: 30,
    oldest_pending_at: "2026-08-01T00:00:00Z",
  },
  outcomes: { banned: 400, warned: 20, failed: 7, skipped_exception: 3 },
  breaker_trips: 0,
  last_activity_at: "2026-08-02T12:00:00Z",
};

function opsScreen(overview: Record<string, unknown> = {}, extra: unknown[] = []) {
  server.use(
    get("/ops/overview", { ...OVERVIEW, ...overview } as never),
    get("/ops/activity", (extra[0] ?? []) as never),
    get("/ops/failures", (extra[1] ?? []) as never),
    get("/ops/jobs", (extra[2] ?? []) as never),
  );
  return renderWithQuery(<Ops />);
}

describe("the dry-run banner", () => {
  it("says loudly when Timothy is only pretending", async () => {
    // The whole point of the screen during a cutover. Zero bans means "nothing needed
    // doing" with dry run off and "nothing was issued" with it on.
    opsScreen({ dry_run: true });

    const banner = await screen.findByRole("status");
    expect(banner).toHaveTextContent(/Dry run is ON/);
    expect(banner).toHaveTextContent(/issuing nothing to Discord/);
  });

  it("says just as clearly when it is not", async () => {
    opsScreen({ dry_run: false });

    expect(await screen.findByRole("status")).toHaveTextContent(
      /Dry run is OFF.*banning and unbanning for real/,
    );
  });

  it("marks the ban tile as unreal while dry run is on", async () => {
    opsScreen({ dry_run: true });

    expect(await screen.findByText("dry run — none real")).toBeInTheDocument();
  });
});

describe("things that are quietly wrong", () => {
  it("raises the alarm when the workers are switched off", async () => {
    // The API still serves and every healthcheck still passes. Nothing else would show
    // that enforcement has stopped happening.
    opsScreen({ workers_enabled: false });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /enforcement worker and sweep scheduler are switched off/,
    );
  });

  it("says when nobody can log in to the web UI", async () => {
    opsScreen({ login_configured: false });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /Discord login is not configured/,
    );
  });

  it("says nothing when everything is as it should be", async () => {
    opsScreen();

    await screen.findByRole("status");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("the tiles and the queue", () => {
  it("shows what Timothy holds", async () => {
    opsScreen();

    expect(await screen.findByText("123")).toBeInTheDocument();
    expect(screen.getByText("3076")).toBeInTheDocument();
    expect(screen.getByText("1 paused")).toBeInTheDocument();
  });

  it("turns the outstanding sweep into progress through the servers", async () => {
    // 30 outstanding of 123 servers is 93 swept — which is the number somebody watching
    // a two-day round actually wants.
    opsScreen();

    const queue = await screen.findByRole("region", { name: "Queue" });
    expect(within(queue).getByText("93 / 123 servers")).toBeInTheDocument();
  });

  it("says plainly when nothing is waiting", async () => {
    opsScreen({
      queue: { ...OVERVIEW.queue, pending: 0, oldest_pending_at: null },
    });

    const queue = await screen.findByRole("region", { name: "Queue" });
    expect(within(queue).getByText("nothing waiting")).toBeInTheDocument();
  });
});

describe("failures", () => {
  it("groups a server's failures into one line", async () => {
    opsScreen({}, [
      [],
      [
        {
          guild_id: "100000000000000002",
          guild_name: "Neon Atrium",
          reason: "Timothy cannot ban this member",
          count: 412,
          latest_at: "2026-08-02T00:00:00Z",
        },
      ],
    ]);

    const card = await screen.findByRole("region", { name: "Failures" });
    expect(await within(card).findByText("412")).toBeInTheDocument();
    expect(within(card).getByText(/Timothy cannot ban this member/)).toBeInTheDocument();
  });

  it("names the server that is failing, and keeps its ID", async () => {
    // Whoever reads this has to go and talk to somebody about it.
    opsScreen({}, [
      [],
      [
        {
          guild_id: "100000000000000002",
          guild_name: "Neon Atrium",
          reason: "Timothy cannot ban this member",
          count: 412,
          latest_at: "2026-08-02T00:00:00Z",
        },
      ],
    ]);

    const card = await screen.findByRole("region", { name: "Failures" });
    expect(await within(card).findByText("Neon Atrium")).toBeInTheDocument();
    expect(within(card).getByText("100000000000000002")).toBeInTheDocument();
  });

  it("falls back to the ID for a server Timothy has left", async () => {
    // Outcomes outlive the guild row on purpose, so these rows have no name to show.
    opsScreen({}, [
      [],
      [
        {
          guild_id: "100000000000000002",
          guild_name: null,
          reason: "Timothy cannot ban this member",
          count: 1,
          latest_at: "2026-08-02T00:00:00Z",
        },
      ],
    ]);

    const card = await screen.findByRole("region", { name: "Failures" });
    expect(await within(card).findByText("100000000000000002")).toBeInTheDocument();
  });

  it("says so when nothing is failing", async () => {
    opsScreen();

    const card = await screen.findByRole("region", { name: "Failures" });
    expect(await within(card).findByText("Nothing is failing.")).toBeInTheDocument();
  });
});

describe("activity", () => {
  it("names the audit actions in words", async () => {
    opsScreen({}, [
      [
        { day: "2026-08-01", series: "enforcement.ban", count: 3 },
        { day: "2026-08-02", series: "enforcement.ban", count: 5 },
      ],
    ]);

    const card = await screen.findByRole("region", { name: "Activity" });
    expect(await within(card).findByText("Bans issued")).toBeInTheDocument();
    expect(within(card).getByText("8")).toBeInTheDocument();
  });

  it("keeps dry-run intentions apart from the real thing, and labels them", async () => {
    // A bare `enforcement.dry_run` count cannot answer "how many bans would that have
    // been" — a warn and a ban are the same action with different consequences.
    opsScreen({}, [
      [
        { day: "2026-08-01", series: "enforcement.dry_run:ban", count: 2935 },
        { day: "2026-08-01", series: "enforcement.dry_run:warn", count: 12 },
      ],
    ]);

    const card = await screen.findByRole("region", { name: "Activity" });
    expect(await within(card).findByText("Would have banned")).toBeInTheDocument();
    expect(within(card).getByText("Would have warned")).toBeInTheDocument();
    expect(within(card).getByText("2935")).toBeInTheDocument();
    expect(within(card).getAllByText("dry run").length).toBe(2);
  });

  it("sorts the busiest thing first", async () => {
    opsScreen({}, [
      [
        { day: "2026-08-01", series: "listing.create", count: 1 },
        { day: "2026-08-01", series: "enforcement.ban", count: 99 },
      ],
    ]);

    const card = await screen.findByRole("region", { name: "Activity" });
    await within(card).findByText("Bans issued");
    const rows = within(card).getAllByRole("row").slice(1);
    expect(rows[0]).toHaveTextContent("Bans issued");
  });

  it("shows an unrecognised action rather than hiding it", async () => {
    // A new audit action added on the backend must not silently vanish from this page.
    opsScreen({}, [[{ day: "2026-08-01", series: "something.new", count: 1 }]]);

    const card = await screen.findByRole("region", { name: "Activity" });
    expect(await within(card).findByText("something.new")).toBeInTheDocument();
  });
});

describe("the job list", () => {
  it("shows an abandoned job with the reason it gave up", async () => {
    opsScreen({}, [
      [],
      [],
      [
        {
          id: 7,
          kind: "enforce_listing",
          payload: { listing_id: 3 },
          run_after: "2026-08-02T00:00:00Z",
          attempts: 5,
          status: "failed",
          last_error: "no such listing: 3",
          created_at: "2026-08-01T00:00:00Z",
        },
      ],
    ]);

    const card = await screen.findByRole("region", { name: "Jobs" });
    expect(await within(card).findByText("no such listing: 3")).toBeInTheDocument();
    expect(within(card).getByText("failed")).toBeInTheDocument();
  });

  it("explains why there is no retry button", async () => {
    // Retrying an abandoned job reliably does nothing — the failures worth retrying are
    // recorded against the server and picked up by the sweep.
    opsScreen();

    const card = await screen.findByRole("region", { name: "Jobs" });
    expect(within(card).getByText(/deliberately no retry here/)).toBeInTheDocument();
    expect(
      within(card).queryByRole("button", { name: /retry/i }),
    ).not.toBeInTheDocument();
  });

  it("filters at the backend rather than in the table", async () => {
    const { user } = opsScreen();
    await screen.findByLabelText("Filter by status");

    // Registered after the screen is up: `server.use` prepends, so this has to come
    // second to win over the one `opsScreen` installed.
    const asked: Array<string | null> = [];
    server.use(
      http.get(apiUrl("/ops/jobs"), ({ request }) => {
        asked.push(new URL(request.url).searchParams.get("status"));
        return HttpResponse.json([]);
      }),
    );

    await user.selectOptions(screen.getByLabelText("Filter by status"), "failed");

    await waitFor(() => expect(asked.at(-1)).toBe("failed"));
  });
});
