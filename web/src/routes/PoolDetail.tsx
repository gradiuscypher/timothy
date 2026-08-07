import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";

import type { BulkResult } from "@/api/client";
import {
  useBulkDeleteListings,
  useBulkListings,
  useCreateListing,
  useDeleteListing,
  useDeletePool,
  useListings,
  usePool,
  useSignedIn,
  useUpdatePool,
  useUserNames,
} from "@/api/hooks";
import {
  ActorRef,
  Badge,
  Button,
  Card,
  CardTitle,
  Cell,
  Confirm,
  Empty,
  ErrorNote,
  Field,
  Input,
  Loading,
  PageTitle,
  Row,
  Table,
  Textarea,
  UserName,
  When,
} from "@/components/ui";
import { actorIds } from "@/components/actors";

const PAGE_SIZE = 50;

/**
 * One pool: who is on it, and everything that can be done to that.
 *
 * The listing table is the reason phase 6 exists. `global` carries thousands of rows
 * after the migration, so it is searched and paged rather than rendered — see
 * `ListingPage` on the backend for why the cursor is an id.
 */
export function PoolDetail({ name }: { name: string }) {
  const pool = usePool(name);
  const session = useSignedIn();
  const canManage = session.data?.manages_pools ?? false;

  if (pool.isPending) return <Loading what={name} />;
  if (pool.isError) return <ErrorNote error={pool.error} />;

  return (
    <>
      <PageTitle>{pool.data.name}</PageTitle>
      {pool.data.description ? (
        <p className="-mt-3 mb-5 text-sm text-surface-muted">{pool.data.description}</p>
      ) : null}

      <div className="space-y-4">
        <Listings name={name} canManage={canManage} />
        {canManage ? (
          <>
            <div className="grid gap-4 lg:grid-cols-2">
              <BulkAdd name={name} />
              <BulkRemove name={name} />
            </div>
            <PoolSettings
              name={name}
              description={pool.data.description}
              currentName={pool.data.name}
            />
          </>
        ) : null}
      </div>
    </>
  );
}

// -- the table -------------------------------------------------------------------------

function Listings({ name, canManage }: { name: string; canManage: boolean }) {
  const [q, setQ] = useState("");
  const [cursors, setCursors] = useState<Array<number | null>>([null]);
  const page = cursors[cursors.length - 1] ?? null;

  const listings = useListings(name, { q, limit: PAGE_SIZE, afterId: page });
  // Everybody this page names: the listed users, and whoever listed them. One resolution
  // for the whole table rather than one per row.
  const names = useUserNames([
    ...(listings.data?.listings ?? []).map((listing) => listing.user_id),
    ...actorIds((listings.data?.listings ?? []).map((listing) => listing.created_by)),
  ]);
  const remove = useDeleteListing(name);
  const [pending, setPending] = useState<{ userId: string; revert: boolean } | null>(null);

  const search = (value: string) => {
    setQ(value);
    // A new search is a new sequence of pages; keeping the old cursor would start it in
    // the middle of results that no longer exist.
    setCursors([null]);
  };

  return (
    <Card label="Listings">
      {/* Search sits up in the header, away from the add form. Side by side they read as
          one thing, and people fill the search box on their way to adding a listing. */}
      <CardTitle
        action={
          <div className="flex items-center gap-3">
            <span className="text-sm whitespace-nowrap text-surface-muted">
              {listings.data ? `${listings.data.total} listed` : null}
            </span>
            <Input
              value={q}
              onChange={(event) => search(event.target.value)}
              aria-label="Search listings"
              title="Matches the reason, or part of a user ID."
              placeholder="Search listings"
              type="search"
              className="w-56"
            />
          </div>
        }
      >
        Listings
      </CardTitle>

      {canManage ? (
        <div className="mb-3">
          <AddListing name={name} />
        </div>
      ) : null}

      <ErrorNote error={listings.error ?? remove.error} />
      {listings.isPending ? <Loading what="listings" /> : null}
      {listings.data?.listings.length === 0 ? (
        <Empty>{q ? `Nothing matching “${q}”.` : "Nobody is listed on this pool."}</Empty>
      ) : null}

      {listings.data?.listings.length ? (
        <Table head={["User", "Reason", "Added by", "Added", ...(canManage ? [""] : [])]}>
          {listings.data.listings.map((listing) => (
            <Row key={listing.id}>
              <Cell>
                <UserName id={listing.user_id} name={names.data?.get(listing.user_id)} />
              </Cell>
              <Cell>{listing.reason}</Cell>
              <Cell>
                <ActorRef actor={listing.created_by} names={names.data} />
              </Cell>
              <Cell>
                <When iso={listing.created_at} />
              </Cell>
              {canManage ? (
                <Cell className="text-right whitespace-nowrap">
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setPending({ userId: listing.user_id, revert: false })}
                  >
                    Remove
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setPending({ userId: listing.user_id, revert: true })}
                  >
                    Remove &amp; unban
                  </Button>
                </Cell>
              ) : null}
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
          Previous
        </Button>
        <Button
          size="sm"
          disabled={!listings.data?.next_after_id}
          onClick={() =>
            setCursors((previous) => [...previous, listings.data?.next_after_id ?? null])
          }
        >
          Next
        </Button>
      </div>

      <Confirm
        open={pending !== null}
        title={pending?.revert ? "Remove the listing and lift the bans?" : "Remove the listing?"}
        body={
          pending?.revert ? (
            <>
              <p>
                Timothy will unban this user in every server where it can show the ban was
                its own. Bans a server placed itself are never touched.
              </p>
              <p>The listing goes as well, so nothing will re-ban them.</p>
            </>
          ) : (
            <p>
              The listing goes; bans already issued stay in place. Nobody will be banned
              for this pool again.
            </p>
          )
        }
        confirmLabel={pending?.revert ? "Remove and unban" : "Remove"}
        busy={remove.isPending}
        onCancel={() => setPending(null)}
        onConfirm={() => {
          if (!pending) return;
          remove.mutate(pending, { onSuccess: () => setPending(null) });
        }}
      />
    </Card>
  );
}

function AddListing({ name }: { name: string }) {
  const create = useCreateListing(name);
  const [userId, setUserId] = useState("");
  const [reason, setReason] = useState("");

  return (
    <form
      className="flex flex-wrap items-end gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        create.mutate(
          { user_id: userId, reason },
          {
            onSuccess: () => {
              setUserId("");
              setReason("");
            },
          },
        );
      }}
    >
      <Field label="User ID">
        <Input
          value={userId}
          onChange={(event) => setUserId(event.target.value)}
          pattern="\d{1,20}"
          required
          placeholder="242024455190577152"
          className="w-52 font-mono"
        />
      </Field>
      <Field label="Reason">
        <Input
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          required
          placeholder="ban evasion"
          className="w-52"
        />
      </Field>
      <Button type="submit" variant="primary" disabled={create.isPending}>
        Add
      </Button>
      <ErrorNote error={create.error} />
    </form>
  );
}

// -- bulk ------------------------------------------------------------------------------

/** Split a pasted blob into snowflakes: newlines, commas, spaces and `<@…>` all work. */
function parseIds(raw: string): string[] {
  return [...new Set(raw.match(/\d{1,20}/g) ?? [])];
}

function Outcome({ result }: { result: BulkResult }) {
  return (
    <p className="text-sm">
      <Badge tone="ok">{result.applied.length} applied</Badge>{" "}
      {result.skipped.length ? (
        <Badge>{result.skipped.length} skipped, already in that state</Badge>
      ) : null}
    </p>
  );
}

function BulkAdd({ name }: { name: string }) {
  const bulk = useBulkListings(name);
  const [raw, setRaw] = useState("");
  const [reason, setReason] = useState("");
  const [confirming, setConfirming] = useState(false);
  const ids = parseIds(raw);

  return (
    <Card label="Add many">
      <CardTitle>Add many</CardTitle>
      <div className="space-y-3">
        <Field label="Reason" hint="One reason for the whole batch.">
          <Input
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="raid of 2026-08-01"
          />
        </Field>
        <Field
          label="User IDs"
          hint={`Anything with IDs in it — one per line, comma-separated, or pasted mentions. ${ids.length} found.`}
        >
          <Textarea
            rows={5}
            value={raw}
            onChange={(event) => setRaw(event.target.value)}
            placeholder={"242024455190577152\n<@110373943822540800>"}
          />
        </Field>
        <ErrorNote error={bulk.error} />
        {bulk.data ? <Outcome result={bulk.data} /> : null}
        <Button
          variant="primary"
          disabled={ids.length === 0 || reason.length === 0 || bulk.isPending}
          onClick={() => setConfirming(true)}
        >
          List {ids.length} {ids.length === 1 ? "user" : "users"}
        </Button>
      </div>

      <Confirm
        open={confirming}
        title={`List ${ids.length} users on ${name}?`}
        destructive={false}
        confirmLabel="List them"
        busy={bulk.isPending}
        body={
          <>
            <p>
              Each one is enforced immediately in every server subscribed to this pool —
              banned where the subscription is at ban level, reported where it is at warn.
            </p>
            <p>
              A batch this size will trip the per-server safety limit and pause
              enforcement there until somebody resumes it. That is deliberate.
            </p>
          </>
        }
        onCancel={() => setConfirming(false)}
        onConfirm={() =>
          bulk.mutate(
            { reason, user_ids: ids },
            {
              onSuccess: () => {
                setConfirming(false);
                setRaw("");
              },
            },
          )
        }
      />
    </Card>
  );
}

function BulkRemove({ name }: { name: string }) {
  const bulk = useBulkDeleteListings(name);
  const [raw, setRaw] = useState("");
  const [revert, setRevert] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const ids = parseIds(raw);

  return (
    <Card label="Remove many">
      <CardTitle>Remove many</CardTitle>
      <div className="space-y-3">
        <Field label="User IDs" hint={`${ids.length} found.`}>
          <Textarea
            rows={5}
            value={raw}
            onChange={(event) => setRaw(event.target.value)}
          />
        </Field>
        <label className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            checked={revert}
            onChange={(event) => setRevert(event.target.checked)}
            className="mt-1"
          />
          <span>
            Also lift the bans this leaves unjustified
            <span className="block text-xs text-surface-muted">
              Only bans Timothy has a record of issuing itself. A server's own bans are
              never touched.
            </span>
          </span>
        </label>
        <ErrorNote error={bulk.error} />
        {bulk.data ? <Outcome result={bulk.data} /> : null}
        <Button
          variant="danger"
          disabled={ids.length === 0 || bulk.isPending}
          onClick={() => setConfirming(true)}
        >
          Remove {ids.length} {ids.length === 1 ? "listing" : "listings"}
        </Button>
      </div>

      <Confirm
        open={confirming}
        title={`Remove ${ids.length} listings from ${name}?`}
        confirmLabel={revert ? "Remove and unban" : "Remove"}
        busy={bulk.isPending}
        body={
          revert ? (
            <p>
              Timothy will unban each of these in every server where it can show the ban
              was its own. This reaches servers that are not on your screen.
            </p>
          ) : (
            <p>Bans already issued stay in place. Only the listings go.</p>
          )
        }
        onCancel={() => setConfirming(false)}
        onConfirm={() =>
          bulk.mutate(
            { user_ids: ids, revert },
            {
              onSuccess: () => {
                setConfirming(false);
                setRaw("");
              },
            },
          )
        }
      />
    </Card>
  );
}

// -- the pool itself -------------------------------------------------------------------

/**
 * Renaming and deleting.
 *
 * Renaming is web-only in the product — the surrogate key exists so it is possible, and
 * no slash command offers it (PLAN.md). This is the only place it can be done.
 */
function PoolSettings({
  name,
  currentName,
  description,
}: {
  name: string;
  currentName: string;
  description: string | null;
}) {
  const navigate = useNavigate();
  const update = useUpdatePool(name);
  const destroy = useDeletePool();
  const [nextName, setNextName] = useState(currentName);
  const [nextDescription, setNextDescription] = useState(description ?? "");
  const [confirming, setConfirming] = useState<false | { revert: boolean }>(false);

  return (
    <Card label="Pool settings">
      <CardTitle>Pool settings</CardTitle>
      <div className="grid gap-5 lg:grid-cols-2">
        <form
          className="space-y-3"
          onSubmit={(event) => {
            event.preventDefault();
            update.mutate(
              { name: nextName, description: nextDescription || null },
              {
                onSuccess: () => {
                  void navigate({ to: "/pools/$name", params: { name: nextName } });
                },
              },
            );
          }}
        >
          <Field
            label="Name"
            hint="Renaming rewrites nothing else — listings and subscriptions follow the pool, not its name."
          >
            <Input
              value={nextName}
              onChange={(event) => setNextName(event.target.value)}
              maxLength={64}
              required
            />
          </Field>
          <Field label="Description">
            <Input
              value={nextDescription}
              onChange={(event) => setNextDescription(event.target.value)}
            />
          </Field>
          <ErrorNote error={update.error} />
          <Button type="submit" variant="primary" disabled={update.isPending}>
            Save
          </Button>
        </form>

        <div className="space-y-3">
          <p className="text-sm text-surface-muted">
            Deleting a pool removes every listing on it and every subscription to it.
          </p>
          <ErrorNote error={destroy.error} />
          <div className="flex flex-wrap gap-2">
            <Button variant="danger" onClick={() => setConfirming({ revert: false })}>
              Delete pool
            </Button>
            <Button variant="danger" onClick={() => setConfirming({ revert: true })}>
              Delete &amp; unban everyone
            </Button>
          </div>
        </div>
      </div>

      <Confirm
        open={confirming !== false}
        title={`Delete ${currentName}?`}
        confirmLabel="Delete"
        busy={destroy.isPending}
        body={
          confirming && confirming.revert ? (
            <>
              <p>
                Every listing and every subscription goes, and Timothy will unban everyone
                it can show it banned for this pool, in every server that subscribed.
              </p>
              <p>This cannot be undone from here.</p>
            </>
          ) : (
            <p>
              Every listing and every subscription goes. Bans already issued stay in
              place — servers keep the people they have banned.
            </p>
          )
        }
        onCancel={() => setConfirming(false)}
        onConfirm={() => {
          if (!confirming) return;
          destroy.mutate(
            { name, revert: confirming.revert },
            {
              onSuccess: () => {
                void navigate({ to: "/pools" });
              },
            },
          );
        }}
      />
    </Card>
  );
}
