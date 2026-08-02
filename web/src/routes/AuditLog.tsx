import { useState } from "react";

import { useAuditLog } from "@/api/hooks";
import {
  ActorRef,
  Button,
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
 * The append-only record: who did what, and when.
 *
 * Timothy's own actions are in here alongside people's — the ban it issued, the
 * exception it created after an unban, the subscription it made when it joined a server.
 * Those are the ones nobody typed, and so the ones most worth being able to look up.
 *
 * Paged by id rather than offset, because the table only grows at one end and an offset
 * shifts under a reader as new rows arrive.
 */

const ACTIONS = [
  ["", "Everything"],
  ["pool.create", "Pool created"],
  ["pool.update", "Pool changed"],
  ["pool.delete", "Pool deleted"],
  ["listing.create", "User listed"],
  ["listing.delete", "Listing removed"],
  ["subscription.set", "Subscription set"],
  ["subscription.delete", "Unsubscribed"],
  ["exception.create", "Exception added"],
  ["exception.delete", "Exception removed"],
  ["notification_channel.set", "Notification channel set"],
  ["notification_channel.delete", "Notification channel cleared"],
  ["guild.register", "Timothy joined a server"],
  ["guild.deregister", "Timothy left a server"],
  ["guild.enforcement_set", "Enforcement paused or resumed"],
  ["enforcement.ban", "Ban issued"],
  ["enforcement.warn", "Warning posted"],
  ["enforcement.failed", "Enforcement failed"],
  ["enforcement.revert", "Ban lifted"],
  ["enforcement.breaker_tripped", "Safety limit tripped"],
  ["enforcement.dry_run", "Dry run — what would have happened"],
] as const;

const PAGE_SIZE = 50;

export function AuditLog() {
  const [action, setAction] = useState("");
  const [cursors, setCursors] = useState<Array<number | null>>([null]);
  const before = cursors[cursors.length - 1] ?? null;
  const entries = useAuditLog(action, before);

  const oldest = entries.data?.[entries.data.length - 1]?.id ?? null;
  const hasMore = (entries.data?.length ?? 0) === PAGE_SIZE;

  return (
    <>
      <PageTitle
        action={
          <Select
            aria-label="Filter by action"
            value={action}
            className="w-72"
            onChange={(event) => {
              setAction(event.target.value);
              setCursors([null]);
            }}
          >
            {ACTIONS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </Select>
        }
      >
        Audit log
      </PageTitle>

      <Card label="Audit log">
        <ErrorNote error={entries.error} />
        {entries.isPending ? <Loading what="the audit log" /> : null}
        {entries.data?.length === 0 ? <Empty>Nothing recorded.</Empty> : null}

        {entries.data?.length ? (
          <Table head={["When", "Who", "Did what", "To what", "Detail"]}>
            {entries.data.map((entry) => (
              <Row key={entry.id}>
                <Cell className="whitespace-nowrap">
                  <When iso={entry.at} />
                </Cell>
                <Cell>
                  <ActorRef actor={entry.actor} />
                </Cell>
                <Cell className="whitespace-nowrap">{entry.action}</Cell>
                <Cell className="snowflake">{entry.target ?? "—"}</Cell>
                <Cell>
                  {entry.detail ? (
                    <code className="text-xs text-surface-muted">
                      {JSON.stringify(entry.detail)}
                    </code>
                  ) : (
                    "—"
                  )}
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
      </Card>
    </>
  );
}
