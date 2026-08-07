import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { OWNER, apiUrl, get, mockApi, renderApp, server } from "./harness";

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

/**
 * The operations screens are mounted through the router, because they link to each other
 * — the overview points at the queue — and a `Link` without a router above it throws.
 */
function opsScreen(overview: Record<string, unknown> = {}, extra: unknown[] = []) {
  server.use(
    get("/auth/me", OWNER),
    get("/ops/overview", { ...OVERVIEW, ...overview } as never),
    get("/ops/activity", (extra[0] ?? []) as never),
    get("/ops/failures", (extra[1] ?? []) as never),
  );
  return renderApp("/ops");
}

/** Every `/ops/jobs` request made from now on, as its query string. */
function watchJobRequests(): URLSearchParams[] {
  const asked: URLSearchParams[] = [];
  server.use(
    http.get(apiUrl("/ops/jobs"), ({ request }) => {
      asked.push(new URL(request.url).searchParams);
      return HttpResponse.json([]);
    }),
  );
  return asked;
}

/** The queue is its own page, and asks for nothing but the queue. */
function jobsScreen(jobs: unknown[] = []) {
  server.use(get("/auth/me", OWNER), get("/ops/jobs", jobs as never));
  return renderApp("/ops/jobs");
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
    jobsScreen([
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
    ]);

    const card = await screen.findByRole("region", { name: "Jobs" });
    expect(await within(card).findByText("no such listing: 3")).toBeInTheDocument();
    expect(within(card).getByText("failed")).toBeInTheDocument();
  });

  it("explains why an abandoned job still has no retry button", async () => {
    // Bringing a *waiting* job forward is a different thing and is offered. Retrying an
    // abandoned one reliably does nothing — the failures worth retrying are recorded
    // against the server and picked up by the sweep.
    jobsScreen();

    const card = await screen.findByRole("region", { name: "Jobs" });
    expect(within(card).getByText(/still no retry for an\s+abandoned one/)).toBeInTheDocument();
    expect(
      within(card).queryByRole("button", { name: /retry/i }),
    ).not.toBeInTheDocument();
  });

  it("filters at the backend rather than in the table", async () => {
    // Fifty rows of a queue with no ceiling on it. Narrowing what has already been
    // fetched would search the last page rather than the queue.
    const { user } = jobsScreen();
    await screen.findByLabelText("Status");

    // Registered after the screen is up: `server.use` prepends, so this has to come
    // second to win over the one `jobsScreen` installed.
    const asked = watchJobRequests();

    await user.selectOptions(screen.getByLabelText("Status"), "failed");

    await waitFor(() => expect(asked.at(-1)?.get("status")).toBe("failed"));
  });

  it("searches the backend too, and keeps the dropdowns while it does", async () => {
    // The payload is JSON and it is where the IDs are, so "anything queued for this
    // server" is a question only the backend can answer.
    const { user } = jobsScreen();
    await screen.findByLabelText("Search");

    const asked = watchJobRequests();

    await user.selectOptions(screen.getByLabelText("Status"), "failed");
    await user.type(screen.getByLabelText("Search"), "100000000000000002");

    await waitFor(() => {
      expect(asked.at(-1)?.get("q")).toBe("100000000000000002");
      expect(asked.at(-1)?.get("status")).toBe("failed");
    });
  });

  it("starts the paging over when a filter changes", async () => {
    // The cursor is an id from the page you were on. Under a different filter it points
    // into a sequence that no longer exists.
    const { user } = jobsScreen(
      Array.from({ length: 50 }, (_, index) => ({
        id: 100 - index,
        kind: "enforce_guild",
        payload: {},
        run_after: "2026-08-02T00:00:00Z",
        attempts: 0,
        status: "pending",
        last_error: null,
        created_at: "2026-08-01T00:00:00Z",
      })),
    );

    await screen.findByRole("button", { name: "Older" });
    const asked = watchJobRequests();

    await user.click(screen.getByRole("button", { name: "Older" }));
    // The oldest row on the page is the cursor into the next one.
    await waitFor(() => expect(asked.at(-1)?.get("before_id")).toBe("51"));

    await user.selectOptions(screen.getByLabelText("Status"), "failed");

    await waitFor(() => expect(asked.at(-1)?.get("before_id")).toBeNull());
  });

  it("offers a way out of the filters only once they are on", async () => {
    const { user } = jobsScreen();
    await screen.findByLabelText("Status");
    expect(screen.queryByRole("button", { name: "Clear filters" })).not.toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("Status"), "failed");
    await user.click(screen.getByRole("button", { name: "Clear filters" }));

    expect(screen.getByLabelText("Status")).toHaveValue("");
  });
});

// -- every server's settings -----------------------------------------------------------

const CONFIG = {
  guild_id: "100000000000000002",
  name: "Neon Atrium",
  joined_at: "2026-01-01T00:00:00Z",
  enforcement_paused: false,
  ban_subscriptions: 2,
  warn_subscriptions: 1,
  exceptions: 3,
  notification_channel_id: "400000000000000001",
};

/** The operator's inventory of every server, mounted through the router for its links. */
function guildsScreen(configs: unknown[] = [CONFIG]) {
  server.use(get("/auth/me", OWNER), get("/ops/guilds", configs as never));
  return renderApp("/ops/guilds");
}

/** Every `/ops/guilds` request made from now on, as its query string. */
function watchGuildRequests(): URLSearchParams[] {
  const asked: URLSearchParams[] = [];
  server.use(
    http.get(apiUrl("/ops/guilds"), ({ request }) => {
      asked.push(new URL(request.url).searchParams);
      return HttpResponse.json([]);
    }),
  );
  return asked;
}

function guildScreen(config: Record<string, unknown> = {}) {
  server.use(
    get("/auth/me", OWNER),
    get(`/ops/guilds/${CONFIG.guild_id}`, {
      guild: {
        guild_id: CONFIG.guild_id,
        name: "Neon Atrium",
        joined_at: CONFIG.joined_at,
        enforcement_paused: false,
      },
      subscriptions: [],
      exceptions: [],
      notification_channel: null,
      ...config,
    } as never),
  );
  return renderApp(`/ops/guilds/${CONFIG.guild_id}`);
}

describe("every server's settings", () => {
  it("lists servers the operator does not administer", async () => {
    // The whole reason the screen exists: the report comes from a server the person
    // debugging it is not in.
    guildsScreen();

    expect(await screen.findByText("Neon Atrium")).toBeInTheDocument();
  });

  it("counts ban and warn subscriptions apart", async () => {
    // A server subscribed only at warn looks exactly like one that is working, right up
    // until nobody is banned. That is the configuration mistake this page is for.
    guildsScreen();

    const row = (await screen.findByText("Neon Atrium")).closest("tr")!;
    expect(within(row).getByText("2 ban")).toBeInTheDocument();
    expect(within(row).getByText("1 warn")).toBeInTheDocument();
  });

  it("says plainly when a server has subscribed to nothing", async () => {
    guildsScreen([{ ...CONFIG, ban_subscriptions: 0, warn_subscriptions: 0 }]);

    const row = (await screen.findByText("Neon Atrium")).closest("tr")!;
    expect(within(row).getByText("none")).toBeInTheDocument();
  });

  it("marks a paused server", async () => {
    guildsScreen([{ ...CONFIG, enforcement_paused: true }]);

    const row = (await screen.findByText("Neon Atrium")).closest("tr")!;
    expect(within(row).getByText("paused")).toBeInTheDocument();
  });

  it("searches the backend rather than the page", async () => {
    // A name or an ID, and the operator has one or the other. Either way the whole
    // inventory is not in the browser to filter.
    const { user } = guildsScreen();
    await screen.findByLabelText("Search");

    const asked = watchGuildRequests();
    await user.type(screen.getByLabelText("Search"), "atrium");

    await waitFor(() => expect(asked.at(-1)?.get("q")).toBe("atrium"));
  });

  it("shows one server's settings in full", async () => {
    guildScreen({
      subscriptions: [
        {
          guild_id: CONFIG.guild_id,
          pool_id: 1,
          pool_name: "spam",
          level: "warn",
          created_by: "user:200000000000000002",
          created_at: "2026-02-01T00:00:00Z",
        },
      ],
      exceptions: [
        {
          guild_id: CONFIG.guild_id,
          user_id: "300000000000000001",
          reason: "known good",
          created_by: "system",
          created_at: "2026-03-01T00:00:00Z",
        },
      ],
      notification_channel: {
        guild_id: CONFIG.guild_id,
        channel_id: "400000000000000001",
        created_by: "user:200000000000000002",
        created_at: "2026-02-01T00:00:00Z",
      },
    });

    expect(await screen.findByText("spam")).toBeInTheDocument();
    expect(screen.getByText("warn")).toBeInTheDocument();
    expect(screen.getByText("known good")).toBeInTheDocument();
    expect(screen.getByText("400000000000000001")).toBeInTheDocument();
  });

  it("says what is missing rather than showing an empty table", async () => {
    // "Subscribed to nothing" and "reports nowhere" are answers to the question that
    // brought somebody here, and a blank card is not.
    guildScreen();

    expect(
      await screen.findByText(/subscribes to nothing/, { exact: false }),
    ).toBeInTheDocument();
    expect(screen.getByText(/No channel is nominated/)).toBeInTheDocument();
  });

  it("explains a paused server before anything else on the page", async () => {
    guildScreen({
      guild: {
        guild_id: CONFIG.guild_id,
        name: "Neon Atrium",
        joined_at: CONFIG.joined_at,
        enforcement_paused: true,
      },
    });

    const banner = await screen.findByRole("status");
    expect(banner).toHaveTextContent(/Enforcement is paused here/);
  });

  it("offers nothing that changes a setting", async () => {
    // Seeing a setting in order to explain it is not authority over it. Every button
    // `GuildDetail` has is deliberately absent here.
    guildScreen();
    await screen.findByText(/subscribes to nothing/);

    expect(screen.queryByRole("button", { name: /Pause|Resume|Save|Remove/ })).toBeNull();
  });
});

describe("acting on a queued job", () => {
  const WAITING = {
    id: 11,
    kind: "backfill_user_names",
    payload: {},
    run_after: "2026-08-14T00:00:00Z",
    attempts: 0,
    status: "pending",
    last_error: null,
    created_at: "2026-08-07T00:00:00Z",
  };

  /** A queue holding one job, and a record of what was posted about it. */
  function withJob(job: Record<string, unknown> = {}) {
    const posted: string[] = [];
    const row = { ...WAITING, ...job };
    server.use(
      get("/auth/me", OWNER),
      get("/ops/jobs", [row] as never),
      http.post(apiUrl("/ops/jobs/11/run-now"), () => {
        posted.push("run-now");
        return HttpResponse.json({ ...row, run_after: "2026-08-07T00:00:00Z" });
      }),
      http.post(apiUrl("/ops/jobs/11/cancel"), () => {
        posted.push("cancel");
        return HttpResponse.json({ ...row, status: "cancelled" });
      }),
    );
    return { posted, ...renderApp("/ops/jobs") };
  }

  it("says how long until a waiting job runs, rather than when", async () => {
    // A staggered sweep round is *supposed* to sit days out. A bare timestamp makes the
    // reader subtract on every row, which is how a healthy queue reads as a stuck one.
    withJob({ run_after: new Date(Date.now() + 3 * 86_400_000).toISOString() });

    const card = await screen.findByRole("region", { name: "Jobs" });
    expect(await within(card).findByText("in 3d")).toBeInTheDocument();
  });

  it("says plainly when a job is already due", async () => {
    // The one that means the queue is behind, as opposed to scheduled.
    withJob({ run_after: new Date(Date.now() - 60_000).toISOString() });

    const card = await screen.findByRole("region", { name: "Jobs" });
    expect(await within(card).findByText("due now")).toBeInTheDocument();
  });

  it("brings a job forward", async () => {
    const { posted, user } = withJob();
    const card = await screen.findByRole("region", { name: "Jobs" });

    await user.click(await within(card).findByRole("button", { name: "Run now" }));

    await waitFor(() => expect(posted).toEqual(["run-now"]));
  });

  it("asks before dropping one", async () => {
    // Cancelling is not reversible from here, and what queued it decides when it comes
    // back — which is not obvious from a button labelled Cancel.
    const { posted, user } = withJob();
    const card = await screen.findByRole("region", { name: "Jobs" });

    await user.click(await within(card).findByRole("button", { name: "Cancel" }));

    expect(screen.getByRole("dialog")).toHaveTextContent(/nothing puts it back/);
    expect(posted).toEqual([]);
  });

  it("drops it once confirmed", async () => {
    const { posted, user } = withJob();
    const card = await screen.findByRole("region", { name: "Jobs" });
    await user.click(await within(card).findByRole("button", { name: "Cancel" }));

    await user.click(screen.getByRole("button", { name: "Drop it" }));

    await waitFor(() => expect(posted).toEqual(["cancel"]));
  });

  it("offers neither on a job that has already run", async () => {
    // Both would be a claim about what is going to happen, and the work is done.
    withJob({ status: "done" });

    const card = await screen.findByRole("region", { name: "Jobs" });
    await within(card).findByText("done");
    expect(within(card).queryByRole("button", { name: "Run now" })).not.toBeInTheDocument();
    expect(within(card).queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument();
  });
});

describe("reading the queue at a glance", () => {
  /** One job per status, so the badges can be compared against each other. */
  function withStatuses() {
    const base = {
      kind: "enforce_guild",
      payload: { guild_id: "1" },
      run_after: "2026-08-07T00:00:00Z",
      attempts: 0,
      last_error: null,
      created_at: "2026-08-07T00:00:00Z",
    };
    server.use(
      get("/auth/me", OWNER),
      get("/ops/jobs", [
        { ...base, id: 1, status: "pending" },
        { ...base, id: 2, status: "running" },
        { ...base, id: 3, status: "done" },
        { ...base, id: 4, status: "failed" },
      ] as never),
    );
    return renderApp("/ops/jobs");
  }

  it("does not draw the job in flight the same as the ones waiting", async () => {
    // A staggered sweep leaves most of a healthy queue pending, so a page where the one
    // row actually moving looks like the hundred behind it answers the wrong question.
    withStatuses();

    const card = await screen.findByRole("region", { name: "Jobs" });
    const running = await within(card).findByText("running");
    const pending = within(card).getByText("pending");

    expect(running.className).not.toEqual(pending.className);
  });

  it("keeps every status visually distinct from every other", async () => {
    withStatuses();

    const card = await screen.findByRole("region", { name: "Jobs" });
    await within(card).findByText("running");
    const classes = ["pending", "running", "done", "failed"].map(
      (status) => within(card).getByText(status).className,
    );

    expect(new Set(classes).size).toBe(classes.length);
  });
});
