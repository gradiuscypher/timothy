import { useState } from "react";

import type { JobStatus } from "@/api/client";
import { useOpsJobs } from "@/api/hooks";
import {
  Badge,
  Card,
  Cell,
  Empty,
  ErrorNote,
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
] as const;

export function Jobs() {
  const [status, setStatus] = useState<JobStatus | "">("");
  const [kind, setKind] = useState("");
  const jobs = useOpsJobs(status, kind);

  return (
    <>
      <PageTitle
        action={
          <div className="flex gap-2">
            <Select
              aria-label="Filter by status"
              value={status}
              className="w-36"
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
              className="w-52"
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
      </PageTitle>

      <Card label="Jobs">
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
    </>
  );
}
