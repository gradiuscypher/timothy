import { useState } from "react";

import type { JobStatus } from "@/api/client";
import { useOpsJobs } from "@/api/hooks";
import {
  Badge,
  Button,
  Card,
  Cell,
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

const PAGE_SIZE = 50;

export function Jobs() {
  const [status, setStatus] = useState<JobStatus | "">("");
  const [kind, setKind] = useState("");
  const [q, setQ] = useState("");
  const [cursors, setCursors] = useState<Array<number | null>>([null]);
  const before = cursors[cursors.length - 1] ?? null;
  const jobs = useOpsJobs({ status, kind, q }, before);

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
        <ErrorNote error={jobs.error} />
        {jobs.isPending ? <Loading what="jobs" /> : null}
        {jobs.data?.length === 0 ? (
          <Empty>{filtered ? "No jobs match those filters." : "The queue is empty."}</Empty>
        ) : null}

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
          There is deliberately no retry here. A job is only abandoned after exhausting its
          attempts on something running it again would not fix — an unknown kind, or a
          payload missing what its handler needs. The failures worth retrying are recorded
          against the server instead, and the sweep picks those up on its own.
        </p>
      </Card>
    </>
  );
}
