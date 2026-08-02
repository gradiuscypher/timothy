import { useState } from "react";

import type { ActivityPoint, JobStatus, OpsOverview } from "@/api/client";
import {
  useOpsActivity,
  useOpsFailures,
  useOpsJobs,
  useOpsOverview,
} from "@/api/hooks";
import {
  Badge,
  Card,
  CardTitle,
  Cell,
  Empty,
  ErrorNote,
  Loading,
  PageTitle,
  Row,
  Select,
  Snowflake,
  Table,
  When,
} from "@/components/ui";

/**
 * Is this thing working?
 *
 * The operator's screen, and the one written for a specific week: the cutover. Everything
 * on it answers a question somebody asks at 2am — is dry run still on, did the workers
 * stop, which server is producing all the failures, how far through the sweep are we.
 *
 * No charting library. The activity view is a table of counts per day, which is what
 * anybody actually reads off a chart of this shape, and a bar drawn in CSS carries the
 * comparison without 40kB of JavaScript.
 */

const WINDOWS = [
  [7, "7 days"],
  [14, "14 days"],
  [30, "30 days"],
  [90, "90 days"],
] as const;

export function Ops() {
  const [days, setDays] = useState(14);
  const overview = useOpsOverview(days);

  return (
    <>
      <PageTitle
        action={
          <Select
            aria-label="Time window"
            value={days}
            className="w-32"
            onChange={(event) => setDays(Number(event.target.value))}
          >
            {WINDOWS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </Select>
        }
      >
        Operations
      </PageTitle>

      <ErrorNote error={overview.error} />
      {overview.isPending ? <Loading what="the overview" /> : null}

      {overview.data ? (
        <div className="space-y-4">
          <Posture data={overview.data} />
          <Tiles data={overview.data} />
          <div className="grid gap-4 lg:grid-cols-2">
            <Queue data={overview.data} />
            <Failures />
          </div>
          <Activity days={days} />
          <Jobs />
        </div>
      ) : null}
    </>
  );
}

// -- the banner that changes what everything else means --------------------------------

/**
 * The settings the numbers depend on, said before the numbers.
 *
 * `dry_run` is the one that matters: zero bans means "nothing needed doing" with it off
 * and "nothing was issued" with it on, and those are opposite situations. Anyone reading
 * this page during a cutover is reading it to answer exactly that.
 */
function Posture({ data }: { data: OpsOverview }) {
  const problems: string[] = [];
  if (!data.workers_enabled) {
    problems.push(
      "The enforcement worker and sweep scheduler are switched off. The API is serving and the queue is accumulating.",
    );
  }
  if (!data.login_configured) {
    problems.push(
      "Discord login is not configured, so nobody new can sign in to this web UI.",
    );
  }

  return (
    <div className="space-y-3">
      <div
        role="status"
        className={
          data.dry_run
            ? "rounded-md bg-warn/10 px-4 py-3 text-sm text-warn"
            : "rounded-md bg-ok/10 px-4 py-3 text-sm text-ok"
        }
      >
        {data.dry_run ? (
          <>
            <strong>Dry run is ON.</strong> Timothy is recording what it would do and
            issuing nothing to Discord. Every count below is an intention, not an action.
          </>
        ) : (
          <>
            <strong>Dry run is OFF.</strong> Timothy is banning and unbanning for real.
          </>
        )}
      </div>
      {problems.map((problem) => (
        <p key={problem} role="alert" className="rounded-md bg-danger/10 px-4 py-3 text-sm text-danger">
          {problem}
        </p>
      ))}
    </div>
  );
}

// -- tiles -----------------------------------------------------------------------------

function Tile({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border border-surface-border bg-surface-1 px-4 py-3">
      <div className="text-xs font-medium tracking-wide text-surface-muted uppercase">
        {label}
      </div>
      <div className="mt-1 text-2xl font-semibold tabular-nums">{value}</div>
      {hint ? <div className="mt-0.5 text-xs text-surface-muted">{hint}</div> : null}
    </div>
  );
}

function Tiles({ data }: { data: OpsOverview }) {
  const { counts, outcomes } = data;
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
      <Tile
        label="Servers"
        value={String(counts.guilds)}
        hint={counts.guilds_paused ? `${counts.guilds_paused} paused` : "none paused"}
      />
      <Tile label="Pools" value={String(counts.pools)} />
      <Tile label="Listings" value={String(counts.listings)} />
      <Tile
        label="Subscriptions"
        value={String(counts.subscriptions)}
        hint={`${counts.notification_channels} with a channel`}
      />
      <Tile
        label="Bans issued"
        value={String(outcomes.banned)}
        hint={data.dry_run ? "dry run — none real" : "attributable to Timothy"}
      />
      <Tile
        label="Failed"
        value={String(outcomes.failed)}
        hint={`${outcomes.warned} warned, ${outcomes.skipped_exception} excepted`}
      />
    </div>
  );
}

// -- the queue -------------------------------------------------------------------------

function Queue({ data }: { data: OpsOverview }) {
  const { queue, counts } = data;
  const sweepDone = counts.guilds - queue.sweep_outstanding;

  return (
    <Card label="Queue">
      <CardTitle>Queue</CardTitle>
      <Table head={["", "Count"]}>
        <Row>
          <Cell>Waiting</Cell>
          <Cell className="tabular-nums">{queue.pending}</Cell>
        </Row>
        <Row>
          <Cell>Running</Cell>
          <Cell className="tabular-nums">{queue.running}</Cell>
        </Row>
        <Row>
          <Cell>Done</Cell>
          <Cell className="tabular-nums">{queue.done}</Cell>
        </Row>
        <Row>
          <Cell>Abandoned</Cell>
          <Cell className="tabular-nums">
            {queue.failed ? <Badge tone="ban">{queue.failed}</Badge> : 0}
          </Cell>
        </Row>
      </Table>

      <dl className="mt-3 space-y-1 text-sm">
        <div className="flex justify-between gap-3">
          <dt className="text-surface-muted">Sweep progress</dt>
          <dd className="tabular-nums">
            {sweepDone} / {counts.guilds} servers
          </dd>
        </div>
        <div className="flex justify-between gap-3">
          <dt className="text-surface-muted">Oldest waiting job</dt>
          <dd>
            {queue.oldest_pending_at ? (
              <When iso={queue.oldest_pending_at} />
            ) : (
              <span className="text-surface-muted">nothing waiting</span>
            )}
          </dd>
        </div>
        <div className="flex justify-between gap-3">
          <dt className="text-surface-muted">Last recorded action</dt>
          <dd>
            {data.last_activity_at ? (
              <When iso={data.last_activity_at} />
            ) : (
              <span className="text-surface-muted">nothing yet</span>
            )}
          </dd>
        </div>
      </dl>

      <p className="mt-3 text-xs text-surface-muted">
        A sweep round takes about two days across a hundred-odd servers, so this counts
        down slowly. It not moving at all is the thing to worry about.
      </p>
    </Card>
  );
}

// -- failures --------------------------------------------------------------------------

function Failures() {
  const failures = useOpsFailures();

  return (
    <Card label="Failures">
      <CardTitle>Enforcement failures</CardTitle>
      <ErrorNote error={failures.error} />
      {failures.isPending ? <Loading what="failures" /> : null}
      {failures.data?.length === 0 ? (
        <Empty>Nothing is failing.</Empty>
      ) : null}

      {failures.data?.length ? (
        <Table head={["Server", "Count", "Why", "Latest"]}>
          {failures.data.map((group) => (
            <Row key={`${group.guild_id}-${group.reason ?? ""}`}>
              <Cell>
                <Snowflake id={group.guild_id} />
              </Cell>
              <Cell className="tabular-nums">{group.count}</Cell>
              <Cell className="text-surface-muted">{group.reason ?? "—"}</Cell>
              <Cell>
                <When iso={group.latest_at} />
              </Cell>
            </Row>
          ))}
        </Table>
      ) : null}

      <p className="mt-3 text-xs text-surface-muted">
        Grouped by server and cause. The usual shape is one server and one sentence
        repeated — almost always a server that has not given Timothy permission to ban.
        These are retried by the sweep, not by the queue.
      </p>
    </Card>
  );
}

// -- activity --------------------------------------------------------------------------

const SERIES_LABELS: Record<string, string> = {
  "enforcement.ban": "Bans issued",
  "enforcement.warn": "Warnings posted",
  "enforcement.revert": "Bans lifted",
  "enforcement.failed": "Enforcement failed",
  "enforcement.breaker_tripped": "Safety limit tripped",
  "enforcement.dry_run:ban": "Would have banned",
  "enforcement.dry_run:warn": "Would have warned",
  "listing.create": "Users listed",
  "listing.delete": "Listings removed",
  "subscription.set": "Subscriptions set",
  "subscription.delete": "Unsubscribed",
  "exception.create": "Exceptions added",
  "guild.register": "Servers joined",
  "guild.deregister": "Servers left",
};

/** Sum a series across the window, and keep its per-day shape for the sparkline. */
function summarise(points: ActivityPoint[]) {
  const days = [...new Set(points.map((point) => point.day))].sort();
  const bySeries = new Map<string, Map<string, number>>();

  for (const point of points) {
    const row = bySeries.get(point.series) ?? new Map<string, number>();
    row.set(point.day, (row.get(point.day) ?? 0) + point.count);
    bySeries.set(point.series, row);
  }

  return [...bySeries.entries()]
    .map(([series, perDay]) => ({
      series,
      perDay: days.map((day) => perDay.get(day) ?? 0),
      total: [...perDay.values()].reduce((sum, n) => sum + n, 0),
    }))
    .sort((a, b) => b.total - a.total);
}

function Sparkline({ values }: { values: number[] }) {
  const peak = Math.max(...values, 1);
  return (
    <span className="flex h-6 items-end gap-px" aria-hidden="true">
      {values.map((value, index) => (
        <span
          // Positions in a fixed-length series; there is no identity to key on.
          key={index}
          className="w-1.5 rounded-t-xs bg-accent/60"
          style={{ height: `${Math.max((value / peak) * 100, value > 0 ? 8 : 2)}%` }}
        />
      ))}
    </span>
  );
}

function Activity({ days }: { days: number }) {
  const activity = useOpsActivity(days);
  const rows = summarise(activity.data ?? []);

  return (
    <Card label="Activity">
      <CardTitle>Activity, last {days} days</CardTitle>
      <ErrorNote error={activity.error} />
      {activity.isPending ? <Loading what="activity" /> : null}
      {activity.data?.length === 0 ? (
        <Empty>Nothing has been recorded in this window.</Empty>
      ) : null}

      {rows.length ? (
        <Table head={["What", "Total", "Per day"]}>
          {rows.map((row) => (
            <Row key={row.series}>
              <Cell>
                {SERIES_LABELS[row.series] ?? row.series}
                {row.series.startsWith("enforcement.dry_run") ? (
                  <>
                    {" "}
                    <Badge tone="warn">dry run</Badge>
                  </>
                ) : null}
              </Cell>
              <Cell className="tabular-nums">{row.total}</Cell>
              <Cell>
                <Sparkline values={row.perDay} />
              </Cell>
            </Row>
          ))}
        </Table>
      ) : null}

      <p className="mt-3 text-xs text-surface-muted">
        Counted from the audit log, by UTC day. Days with nothing in them are left out
        rather than shown as zero.
      </p>
    </Card>
  );
}

// -- the raw queue ---------------------------------------------------------------------

const JOB_STATUSES: Array<[JobStatus | "", string]> = [
  ["", "Any status"],
  ["pending", "Waiting"],
  ["running", "Running"],
  ["done", "Done"],
  ["failed", "Abandoned"],
];

const JOB_KINDS = [
  ["", "Any kind"],
  ["enforce_listing", "A user was listed"],
  ["enforce_subscription", "A server subscribed"],
  ["enforce_guild", "Sweep / catch-up"],
  ["enforce_guild_user", "An exception was removed"],
  ["revert_listing", "Lift bans for a listing"],
  ["revert_pool", "Lift bans for a pool"],
  ["revert_subscription", "Lift bans for a subscription"],
] as const;

function Jobs() {
  const [status, setStatus] = useState<JobStatus | "">("");
  const [kind, setKind] = useState("");
  const jobs = useOpsJobs(status, kind);

  return (
    <Card label="Jobs">
      <CardTitle
        action={
          <div className="flex gap-2">
            <Select
              aria-label="Filter by status"
              value={status}
              className="h-8 w-36"
              onChange={(event) => setStatus(event.target.value as JobStatus | "")}
            >
              {JOB_STATUSES.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </Select>
            <Select
              aria-label="Filter by kind"
              value={kind}
              className="h-8 w-52"
              onChange={(event) => setKind(event.target.value)}
            >
              {JOB_KINDS.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </Select>
          </div>
        }
      >
        Jobs
      </CardTitle>

      <ErrorNote error={jobs.error} />
      {jobs.isPending ? <Loading what="jobs" /> : null}
      {jobs.data?.length === 0 ? <Empty>No jobs match.</Empty> : null}

      {jobs.data?.length ? (
        <Table head={["Kind", "Status", "Tries", "About", "Created", "Last error"]}>
          {jobs.data.map((job) => (
            <Row key={job.id}>
              <Cell className="whitespace-nowrap">{job.kind}</Cell>
              <Cell>
                <Badge
                  tone={
                    job.status === "failed" ? "ban" : job.status === "done" ? "ok" : "neutral"
                  }
                >
                  {job.status}
                </Badge>
              </Cell>
              <Cell className="tabular-nums">{job.attempts}</Cell>
              <Cell>
                <code className="text-xs text-surface-muted">
                  {JSON.stringify(job.payload)}
                </code>
              </Cell>
              <Cell className="whitespace-nowrap">
                <When iso={job.created_at} />
              </Cell>
              <Cell className="text-danger">{job.last_error ?? ""}</Cell>
            </Row>
          ))}
        </Table>
      ) : null}

      <p className="mt-3 text-xs text-surface-muted">
        There is deliberately no retry here. A job is only abandoned after exhausting its
        attempts on something running it again would not fix — an unknown kind, or a
        payload missing what its handler needs. The failures worth retrying are recorded
        against the server instead, and the sweep picks those up on its own.
      </p>
    </Card>
  );
}
