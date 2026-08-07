import { useEffect, useState } from "react";

import type { JobStatus } from "@/api/client";
import { useJobAction, useOpsJobs } from "@/api/hooks";
import {
  Badge,
  Button,
  Card,
  Cell,
  Confirm,
  Empty,
  ErrorNote,
  Field,
  FilterBar,
  Input,
  Loading,
  PageTitle,
  Row,
  Select,
  Table,
  When,
} from "@/components/ui";
import { useToast } from "@/components/Toast";

/**
 * The queue itself, row by row.
 *
 * Split out of the operations overview, which answers "is this thing working" in a
 * screenful of tiles. This answers a different question — *what* is in the queue — and it
 * is answered by filtering and reading, which is somewhere you stay rather than glance.
 * It is also the widest table in the app: a kind, a JSON payload and an error message on
 * one row, which is why it takes the whole window (`wide` in `router.tsx`).
 *
 * The search reaches into the payload, which is where the IDs are. "Is there anything
 * queued for this server" has no other answer — the kind says `enforce_guild` and the
 * guild it is for is inside the JSON.
 */

const JOB_STATUSES: Array<[JobStatus | "", string]> = [
  ["", "Any status"],
  ["pending", "Waiting"],
  ["running", "Running"],
  ["done", "Done"],
  ["failed", "Abandoned"],
  ["cancelled", "Cancelled"],
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
  ["backfill_user_names", "Look up user names"],
] as const;

const TICK_MS = 30_000;

/**
 * The current time, as state rather than as a call during render.
 *
 * `Date.now()` in a render is impure — the same props would draw differently on a
 * re-render nobody asked for — and the lint rules say so. Holding it in state fixes that
 * and pays for itself: "in 2m" counts down on its own while an operator watches, which is
 * exactly what somebody staring at a queue wants it to do.
 */
function useNow(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), TICK_MS);
    return () => clearInterval(timer);
  }, []);
  return now;
}

/**
 * A job's schedule, said in the terms an operator is actually asking about.
 *
 * "Due now" and "in 3d" are the same column and opposite situations: the first means the
 * queue is behind, the second means the sweep is staggered exactly as designed. A bare
 * timestamp makes the reader do that subtraction on every row, which is how a perfectly
 * healthy queue gets read as a stuck one.
 */
function Due({ iso, status, now }: { iso: string; status: JobStatus; now: number }) {
  const at = new Date(iso);
  if (status !== "pending") return <When iso={iso} />;

  const seconds = (at.getTime() - now) / 1000;
  return (
    <span title={at.toISOString()} className="whitespace-nowrap">
      {seconds <= 0 ? "due now" : <span className="text-surface-muted">in {delay(seconds)}</span>}
    </span>
  );
}

function delay(seconds: number): string {
  if (seconds < 90) return `${Math.round(seconds)}s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m`;
  if (seconds < 172800) return `${Math.round(seconds / 3600)}h`;
  return `${Math.round(seconds / 86400)}d`;
}

/**
 * Each status its own colour, because this column is scanned rather than read.
 *
 * `running` is the one that had to change: it shared the grey of `pending`, so the single
 * row the queue is actually working on looked exactly like the hundred waiting behind it.
 * A staggered sweep means most of a healthy queue is `pending`, and a page where
 * everything is grey answers the wrong question — what is *moving* is the whole reason
 * somebody opened it.
 */
const STATUS_TONE: Record<JobStatus, "neutral" | "ok" | "ban" | "active"> = {
  pending: "neutral",
  running: "active",
  done: "ok",
  failed: "ban",
  // Terminal but not a failure, and deliberately quiet: nothing went wrong, somebody
  // decided this would not run.
  cancelled: "neutral",
};

const PAGE_SIZE = 50;

export function Jobs() {
  const [status, setStatus] = useState<JobStatus | "">("");
  const [kind, setKind] = useState("");
  const [q, setQ] = useState("");
  const [cursors, setCursors] = useState<Array<number | null>>([null]);
  const before = cursors[cursors.length - 1] ?? null;
  const jobs = useOpsJobs({ status, kind, q }, before);
  const runNow = useJobAction("run-now");
  const cancel = useJobAction("cancel");
  const notify = useToast();
  const [pending, setPending] = useState<number | null>(null);
  const now = useNow();

  const oldest = jobs.data?.[jobs.data.length - 1]?.id ?? null;
  const hasMore = (jobs.data?.length ?? 0) === PAGE_SIZE;
  const filtered = status !== "" || kind !== "" || q !== "";

  /** A changed filter is a new sequence of pages, so the cursor into the old one goes. */
  const narrow = (change: () => void) => {
    change();
    setCursors([null]);
  };

  return (
    <>
      <PageTitle>Jobs</PageTitle>

      <FilterBar
        label="Filter the queue"
        onClear={
          filtered
            ? () =>
                narrow(() => {
                  setStatus("");
                  setKind("");
                  setQ("");
                })
            : undefined
        }
      >
        <Field
          label="Search"
          hint="A user or server ID from the payload, or words from the error."
          className="min-w-64 grow"
        >
          <Input
            type="search"
            value={q}
            placeholder="Any payload or error"
            onChange={(event) => narrow(() => setQ(event.target.value))}
          />
        </Field>
        <Field label="Status" className="w-40">
          <Select
            value={status}
            onChange={(event) => narrow(() => setStatus(event.target.value as JobStatus | ""))}
          >
            {JOB_STATUSES.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Kind" className="w-56">
          <Select value={kind} onChange={(event) => narrow(() => setKind(event.target.value))}>
            {JOB_KINDS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </Select>
        </Field>
      </FilterBar>

      <Card label="Jobs">
        <ErrorNote error={jobs.error ?? runNow.error ?? cancel.error} />
        {jobs.isPending ? <Loading what="jobs" /> : null}
        {jobs.data?.length === 0 ? (
          <Empty>{filtered ? "No jobs match those filters." : "The queue is empty."}</Empty>
        ) : null}

        {jobs.data?.length ? (
          <Table
            head={["Kind", "Status", "Tries", "About", "Runs", "Created", "Last error", ""]}
          >
            {jobs.data.map((job) => (
              <Row key={job.id}>
                <Cell className="whitespace-nowrap">{job.kind}</Cell>
                <Cell>
                  <Badge tone={STATUS_TONE[job.status]}>{job.status}</Badge>
                </Cell>
                <Cell className="tabular-nums">{job.attempts}</Cell>
                <Cell>
                  <code className="text-xs text-surface-muted">
                    {JSON.stringify(job.payload)}
                  </code>
                </Cell>
                <Cell>
                  <Due iso={job.run_after} status={job.status} now={now} />
                </Cell>
                <Cell className="whitespace-nowrap">
                  <When iso={job.created_at} />
                </Cell>
                <Cell className="text-danger">{job.last_error ?? ""}</Cell>
                <Cell className="text-right whitespace-nowrap">
                  {job.status === "pending" ? (
                    <>
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={runNow.isPending}
                        onClick={() =>
                          runNow.mutate(job.id, {
                            onSuccess: () => notify("Job moved to the front of the queue."),
                          })
                        }
                      >
                        Run now
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={cancel.isPending}
                        onClick={() => setPending(job.id)}
                      >
                        Cancel
                      </Button>
                    </>
                  ) : null}
                </Cell>
              </Row>
            ))}
          </Table>
        ) : null}

        <div className="mt-3 flex items-center justify-between">
          <Button
            size="sm"
            disabled={cursors.length === 1}
            onClick={() => setCursors((previous) => previous.slice(0, -1))}
          >
            Newer
          </Button>
          <Button
            size="sm"
            disabled={!hasMore}
            onClick={() => setCursors((previous) => [...previous, oldest])}
          >
            Older
          </Button>
        </div>

        <p className="mt-3 text-xs text-surface-muted">
          A waiting job can be brought forward or dropped. There is still no retry for an
          abandoned one: a job is only abandoned after exhausting its attempts on something
          running it again would not fix — an unknown kind, or a payload missing what its
          handler needs. The failures worth retrying are recorded against the server
          instead, and the sweep picks those up on its own.
        </p>

        <Confirm
          open={pending !== null}
          title="Drop this job?"
          body={
            <>
              <p>
                It will not run, and nothing puts it back. Whatever queued it will queue it
                again in its own time — the next sweep round, the next backfill round, or
                the next change that implies it.
              </p>
              <p>Work already done is untouched. This only stops what had not started.</p>
            </>
          }
          confirmLabel="Drop it"
          busy={cancel.isPending}
          onCancel={() => setPending(null)}
          onConfirm={() => {
            if (pending === null) return;
            cancel.mutate(pending, {
              onSuccess: () => {
                notify("Job cancelled.");
                setPending(null);
              },
            });
          }}
        />
      </Card>
    </>
  );
}
