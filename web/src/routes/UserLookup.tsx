import { Link, useNavigate } from "@tanstack/react-router";
import { useState } from "react";

import { useNameSearch, useUserListings, useUserNames } from "@/api/hooks";
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
 * A snowflake, bare or wrapped in the mention syntax somebody pasted.
 *
 * Anchored, which is the whole point: `Nuisance2024` contains digits and is plainly not
 * an ID, and matching them anywhere would send that straight to a lookup of user 2024.
 * Anything this does not match is a name to search for instead.
 */
const SNOWFLAKE = /^(?:<@!?)?(\d{1,20})>?$/;

/** What `/users/search` caps its answer at — `usernames.MAX_MATCHES` on the backend. */
const MAX_MATCHES = 25;

/**
 * Why a user is listed, across every pool.
 *
 * The one screen open to anyone in a server Timothy is in, and the one a moderator with
 * no administrator anywhere actually needs — it is the web half of `/get_user_bans`.
 * ADR 0001 names this as the first rule it expects to relax.
 *
 * One box, two things typed into it. An ID is what the route takes and what everything
 * downstream is keyed by; a name is what a moderator actually remembers, and it resolves
 * to candidates rather than to a user — Timothy's name cache holds one name per ID and
 * nothing makes them unique, so which of two people called the same thing was meant is a
 * question only the reader can answer.
 */
export function UserLookup({ userId }: { userId?: string }) {
  const navigate = useNavigate();
  const [typed, setTyped] = useState(userId ?? "");
  // What was submitted as a name, which is not what is being typed: firing a search per
  // keystroke would scan the name table for every prefix of a word on its way in.
  const [query, setQuery] = useState("");
  const candidates = useNameSearch(query);
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
            const id = SNOWFLAKE.exec(typed.trim())?.[1];
            if (id) {
              setQuery("");
              void navigate({ to: "/users/$userId", params: { userId: id } });
            } else {
              setQuery(typed.trim());
            }
          }}
        >
          <div className="min-w-64 grow">
            <Field
              label="Discord user ID or name"
              hint="A pasted mention works too. Anything else is searched for as a name."
            >
              <Input
                value={typed}
                onChange={(event) => setTyped(event.target.value)}
                placeholder="242024455190577152"
              />
            </Field>
          </div>
          <Button type="submit" variant="primary">
            Look up
          </Button>
        </form>
      </Card>

      {query ? (
        <Card className="mb-4">
          <CardTitle>Users called “{query}”</CardTitle>
          {candidates.isPending ? <Loading what="matches" /> : null}
          <ErrorNote error={candidates.error} />
          {candidates.data?.length === 0 ? (
            <Empty>
              Nobody Timothy has seen is called that. Names are only known for users
              Timothy has come across, so an ID is the surer way in.
            </Empty>
          ) : null}
          {candidates.data?.length ? (
            <Table head={["Name", "User ID", "Name last seen"]}>
              {candidates.data.map((candidate) => (
                <Row key={candidate.user_id}>
                  <Cell>
                    <Link
                      to="/users/$userId"
                      params={{ userId: candidate.user_id }}
                      className="font-medium text-accent hover:underline"
                    >
                      {candidate.name}
                    </Link>
                  </Cell>
                  <Cell>
                    <span className="snowflake">{candidate.user_id}</span>
                  </Cell>
                  <Cell>
                    <When iso={candidate.observed_at} />
                  </Cell>
                </Row>
              ))}
            </Table>
          ) : null}
          {/* The backend answers with at most `MAX_MATCHES`, and a full list is
              indistinguishable from a complete one without saying so. */}
          {candidates.data?.length === MAX_MATCHES ? (
            <p className="mt-3 text-xs text-surface-muted">
              The first {MAX_MATCHES} matches. There may be more — type more of the name.
            </p>
          ) : null}
        </Card>
      ) : null}

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
