import { Link, useNavigate } from "@tanstack/react-router";
import { useState } from "react";

import { useUserListings, useUserNames } from "@/api/hooks";
import {
  ActorRef,
  Button,
  Card,
  CardTitle,
  Cell,
  Empty,
  ErrorNote,
  Field,
  Input,
  Loading,
  PageTitle,
  Row,
  Table,
  When,
} from "@/components/ui";
import { actorIds } from "@/components/actors";

/**
 * Why a user is listed, across every pool.
 *
 * The one screen open to anyone in a server Timothy is in, and the one a moderator with
 * no administrator anywhere actually needs — it is the web half of `/get_user_bans`.
 * ADR 0001 names this as the first rule it expects to relax.
 */
export function UserLookup({ userId }: { userId?: string }) {
  const navigate = useNavigate();
  const [typed, setTyped] = useState(userId ?? "");
  const listings = useUserListings(userId ?? "");
  // The user being looked up, and whoever listed them. The looked-up ID goes in even when
  // nothing lists them: the heading names them either way.
  const names = useUserNames([
    ...(userId ? [userId] : []),
    ...actorIds((listings.data ?? []).map((listing) => listing.created_by)),
  ]);

  return (
    <>
      <PageTitle>Look up a user</PageTitle>

      <Card className="mb-4">
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            const id = /\d{1,20}/.exec(typed)?.[0];
            if (id) void navigate({ to: "/users/$userId", params: { userId: id } });
          }}
        >
          <div className="min-w-64 grow">
            <Field label="Discord user ID" hint="A pasted mention works too.">
              <Input
                value={typed}
                onChange={(event) => setTyped(event.target.value)}
                placeholder="242024455190577152"
                className="font-mono"
              />
            </Field>
          </div>
          <Button type="submit" variant="primary">
            Look up
          </Button>
        </form>
      </Card>

      {userId ? (
        <Card>
          <CardTitle>
            {names.data?.get(userId) ? (
              <span className="flex flex-wrap items-baseline gap-2">
                <span>{names.data.get(userId)}</span>
                <span className="snowflake text-sm text-surface-muted">{userId}</span>
              </span>
            ) : (
              <span className="snowflake">{userId}</span>
            )}
          </CardTitle>
          {listings.isPending ? <Loading what="listings" /> : null}
          <ErrorNote error={listings.error} />
          {listings.data?.length === 0 ? (
            <Empty>
              Not listed on any pool. Nothing Timothy does would ban this user.
            </Empty>
          ) : null}
          {listings.data?.length ? (
            <Table head={["Pool", "Reason", "Added by", "Added"]}>
              {listings.data.map((listing) => (
                <Row key={listing.id}>
                  <Cell>
                    <Link
                      to="/pools/$name"
                      params={{ name: listing.pool_name }}
                      className="font-medium text-accent hover:underline"
                    >
                      {listing.pool_name}
                    </Link>
                  </Cell>
                  <Cell>{listing.reason}</Cell>
                  <Cell>
                    <ActorRef actor={listing.created_by} names={names.data} />
                  </Cell>
                  <Cell>
                    <When iso={listing.created_at} />
                  </Cell>
                </Row>
              ))}
            </Table>
          ) : null}
          <p className="mt-3 text-xs text-surface-muted">
            A listing is a record, not an action. Whether this user is banned in a
            particular server depends on that server's subscriptions and exceptions.
          </p>
        </Card>
      ) : null}
    </>
  );
}
