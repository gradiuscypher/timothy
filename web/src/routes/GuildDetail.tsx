import { Fragment, useState } from "react";

import type { OutcomeStatus, SubscriptionLevel } from "@/api/client";
import {
  useBanFailureDiagnosis,
  useBanFailures,
  useClearNotificationChannel,
  useCreateException,
  useDeleteException,
  useDeleteSubscription,
  useEnforcement,
  useExceptions,
  useGuild,
  useGuildDiagnostics,
  useNotificationChannel,
  usePauseEnforcement,
  usePools,
  useRefreshDiagnostics,
  useSetNotificationChannel,
  useSetSubscription,
  useSubscriptions,
  useUserNames,
} from "@/api/hooks";
import {
  ActorRef,
  Badge,
  Banner,
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
  Select,
  Snowflake,
  Table,
  UserName,
  When,
} from "@/components/ui";
import { actorIds } from "@/components/actors";
import { useToast } from "@/components/Toast";

/** Everything one server's administrators control, plus what Timothy has done there. */
export function GuildDetail({ guildId }: { guildId: string }) {
  const guild = useGuild(guildId);

  if (guild.isPending) return <Loading what="this server" />;
  if (guild.isError) return <ErrorNote error={guild.error} />;

  return (
    <>
      <PageTitle
        action={<PauseSwitch guildId={guildId} paused={guild.data.enforcement_paused} />}
      >
        {/* The name is what a person recognises; the ID is what they paste into
            Discord's search, so both are here and the ID never disappears. */}
        {guild.data.name ? (
          <span className="flex flex-col gap-0.5">
            <span>{guild.data.name}</span>
            <Snowflake id={guildId} className="text-sm font-normal text-surface-muted" />
          </span>
        ) : (
          <span className="snowflake text-lg">{guildId}</span>
        )}
      </PageTitle>

      {guild.data.enforcement_paused ? (
        <Banner className="mb-4">
          Enforcement is paused here. Timothy is recording nothing and issuing nothing in
          this server. Resuming queues a catch-up over everything missed.
        </Banner>
      ) : null}

      <BanReadiness guildId={guildId} />

      <div className="space-y-4">
        <Subscriptions guildId={guildId} />
        <div className="grid gap-4 lg:grid-cols-2">
          <Exceptions guildId={guildId} />
          <NotificationChannel guildId={guildId} />
        </div>
        <UnbannableRoles guildId={guildId} />
        <BanFailures guildId={guildId} />
        <History guildId={guildId} />
      </div>
    </>
  );
}

function PauseSwitch({ guildId, paused }: { guildId: string; paused: boolean }) {
  const pause = usePauseEnforcement(guildId);
  const notify = useToast();
  return (
    <div className="flex items-center gap-2">
      <ErrorNote error={pause.error} />
      <Button
        variant={paused ? "primary" : "secondary"}
        disabled={pause.isPending}
        onClick={() =>
          pause.mutate(!paused, {
            onSuccess: () => notify(paused ? "Enforcement resumed." : "Enforcement paused."),
          })
        }
      >
        {paused ? "Resume enforcement" : "Pause enforcement"}
      </Button>
    </div>
  );
}

// -- can Timothy ban here at all -------------------------------------------------------

/**
 * The one thing on this page that means nothing else on it works.
 *
 * Three states, and the third is the one worth being careful about. Timothy can ban;
 * Timothy cannot ban; and *nobody has looked* — a server the bot has never reported on
 * (ADR 0016). The last must not render as the first: an all-clear nobody measured is the
 * only answer here worse than no answer.
 */
function BanReadiness({ guildId }: { guildId: string }) {
  const diagnostics = useGuildDiagnostics(guildId);
  if (diagnostics.isPending || diagnostics.isError) return null;

  if (diagnostics.data === null) {
    return (
      <Banner className="mb-4">
        Timothy has not checked this server's setup yet. It does that within a few minutes
        of connecting, so this usually clears itself — until it does, nothing below says
        whether Timothy can actually ban here.
      </Banner>
    );
  }

  if (!diagnostics.data.can_ban) {
    return (
      <Banner tone="danger" role="alert" className="mb-4">
        <strong>Timothy cannot ban anyone in this server.</strong> It has not been granted
        the Ban Members permission, so every ban-level subscription below will fail until
        an administrator grants it in Server Settings → Roles.
      </Banner>
    );
  }

  if (diagnostics.data.stale) {
    return (
      <Banner className="mb-4">
        Timothy last checked this server's setup <When iso={diagnostics.data.observed_at} />.
        Anything changed in Discord since then may not be reflected below.
      </Banner>
    );
  }

  return null;
}

// -- roles out of reach ----------------------------------------------------------------

/**
 * The roles Timothy can never ban anybody out of.
 *
 * A role at *exactly* Timothy's own position counts, which is the whole reason this
 * exists as a list rather than as a number to compare: Discord's own settings screen
 * shows the two level with each other and gives no hint that level means out of reach.
 */
function UnbannableRoles({ guildId }: { guildId: string }) {
  const diagnostics = useGuildDiagnostics(guildId);
  const refresh = useRefreshDiagnostics(guildId);
  const notify = useToast();

  const data = diagnostics.data ?? null;
  const managed = data?.unbannable_roles.filter((role) => role.managed) ?? [];
  const ordinary = data?.unbannable_roles.filter((role) => !role.managed) ?? [];

  return (
    <Card label="Roles Timothy cannot ban">
      <CardTitle
        action={
          <Button
            size="sm"
            variant="secondary"
            disabled={refresh.isPending}
            onClick={() =>
              refresh.mutate(undefined, {
                onSuccess: () =>
                  notify("Re-check requested. Timothy will look again shortly."),
              })
            }
          >
            Check again
          </Button>
        }
      >
        Roles Timothy cannot ban
      </CardTitle>
      <p className="mb-3 text-sm text-surface-muted">
        Discord will not let Timothy ban anyone whose highest role sits <strong>at or
        above</strong> its own. Level with counts — that is the one people miss. Move
        Timothy's role higher in Server Settings → Roles to shrink this list.
      </p>

      <ErrorNote error={diagnostics.error ?? refresh.error} />
      {diagnostics.isPending ? <Loading what="this server's roles" /> : null}
      {data === null && !diagnostics.isPending ? (
        <Empty>Timothy has not checked this server yet.</Empty>
      ) : null}

      {data?.unbannable_roles.length === 0 ? (
        <Empty>
          Timothy outranks every role here. Nobody is out of reach on role position alone.
        </Empty>
      ) : null}

      {data?.unbannable_roles.length ? (
        <>
          <Table head={["Role", "Position", "Members"]}>
            {[...ordinary, ...managed].map((role) => (
              <Row key={role.role_id}>
                <Cell className="font-medium">
                  {role.name}
                  {role.managed ? (
                    <>
                      {" "}
                      <Badge>managed by Discord</Badge>
                    </>
                  ) : null}
                </Cell>
                <Cell className="text-surface-muted">{role.position}</Cell>
                <Cell className="text-surface-muted">
                  {role.member_count ?? "—"}
                </Cell>
              </Row>
            ))}
          </Table>
          <p className="mt-3 text-xs text-surface-muted">
            {data.unbannable_members === null ? (
              <>
                Timothy could not count who holds these roles, so the numbers above are
                left blank rather than shown as zero.
              </>
            ) : (
              <>
                Up to {data.unbannable_members} people are out of reach. "Up to", because
                anyone holding more than one of these roles is counted once per role.
              </>
            )}{" "}
            Timothy's own highest role is{" "}
            <strong>{data.top_role_name ?? "unnamed"}</strong> at position{" "}
            {data.top_role_position}. Checked <When iso={data.observed_at} />.
            {managed.length ? (
              <>
                {" "}
                Roles marked <em>managed by Discord</em> belong to an integration and
                cannot be taken off anyone by hand — raising Timothy is the only fix for
                those.
              </>
            ) : null}
          </p>
        </>
      ) : null}
    </Card>
  );
}

// -- bans that failed ------------------------------------------------------------------

const BLOCKER_COPY = {
  no_ban_permission:
    "Timothy has not been granted the Ban Members permission in this server.",
  guild_owner: "This person owns the server. Nothing Discord offers can ban them.",
  outranked: "This person holds a role at or above Timothy's own.",
  left_guild:
    "This person is not in the server now. Timothy bans them at the door if they return.",
  unknown: "Timothy could not work out why. Discord's own words are below.",
} as const;

/**
 * Every ban Timothy tried here and could not issue, each one explainable.
 *
 * The list is free — it is the `failed` outcomes read back. The explanation is not: it
 * costs a live Discord lookup, so it is fetched when somebody opens a row, and it
 * describes the world *now* rather than when the ban failed. That is deliberate: a person
 * reading this is about to go and move a role, and wants to know whether it would work
 * today.
 */
function BanFailures({ guildId }: { guildId: string }) {
  const failures = useBanFailures(guildId);
  const names = useUserNames((failures.data ?? []).map((failure) => failure.user_id));
  const [open, setOpen] = useState<string | null>(null);

  return (
    <Card label="Failed bans">
      <CardTitle>Failed bans</CardTitle>
      <p className="mb-3 text-sm text-surface-muted">
        Bans Timothy was asked to issue here and could not. Open one to see why, checked
        against Discord as things stand right now.
      </p>

      <ErrorNote error={failures.error} />
      {failures.isPending ? <Loading what="failed bans" /> : null}
      {failures.data?.length === 0 ? (
        <Empty>Nothing Timothy tried to ban here has failed.</Empty>
      ) : null}

      {failures.data?.length ? (
        <Table head={["User", "Pool", "What Discord said", "When", ""]}>
          {failures.data.map((failure) => (
            <Fragment key={`${failure.user_id}-${failure.pool_id}`}>
              <Row>
                <Cell>
                  <UserName id={failure.user_id} name={names.data?.get(failure.user_id)} />
                </Cell>
                <Cell>{failure.pool_name ?? <em>deleted pool</em>}</Cell>
                <Cell className="text-surface-muted">{failure.reason ?? "—"}</Cell>
                <Cell>
                  <When iso={failure.attempted_at} />
                </Cell>
                <Cell className="text-right">
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-expanded={open === failure.user_id}
                    onClick={() =>
                      setOpen(open === failure.user_id ? null : failure.user_id)
                    }
                  >
                    {open === failure.user_id ? "Hide" : "Why?"}
                  </Button>
                </Cell>
              </Row>
              {open === failure.user_id ? (
                <Row>
                  <Cell colSpan={5}>
                    <Diagnosis guildId={guildId} userId={failure.user_id} />
                  </Cell>
                </Row>
              ) : null}
            </Fragment>
          ))}
        </Table>
      ) : null}
    </Card>
  );
}

function Diagnosis({ guildId, userId }: { guildId: string; userId: string }) {
  const diagnosis = useBanFailureDiagnosis(guildId, userId);

  if (diagnosis.isPending) return <Loading what="the reason" />;
  if (diagnosis.isError) return <ErrorNote error={diagnosis.error} />;

  const { blocker, blocking_roles, timothy_top_role_name, timothy_top_role_position } =
    diagnosis.data;

  return (
    <div className="space-y-2 py-2">
      <p className="text-sm">
        <Badge tone={blocker === "left_guild" ? "neutral" : "ban"}>
          {blocker.replaceAll("_", " ")}
        </Badge>{" "}
        {BLOCKER_COPY[blocker]}
      </p>

      {blocking_roles.length ? (
        <p className="text-sm text-surface-muted">
          In the way:{" "}
          {blocking_roles.map((role, index) => (
            <Fragment key={role.role_id}>
              {index ? ", " : ""}
              <strong>{role.name}</strong> (position {role.position})
            </Fragment>
          ))}
          . Timothy sits at position {timothy_top_role_position} as{" "}
          <strong>{timothy_top_role_name ?? "an unnamed role"}</strong>. Move Timothy's
          role above{" "}
          {blocking_roles.length === 1 ? "that one" : "all of those"}, or take{" "}
          {blocking_roles.length === 1 ? "it" : "them"} off this person.
        </p>
      ) : null}

      {diagnosis.data.detail ? (
        <p className="text-xs text-surface-muted">
          Discord said: <span className="font-mono">{diagnosis.data.detail}</span>
        </p>
      ) : null}
    </div>
  );
}

// -- subscriptions ---------------------------------------------------------------------

const LEVELS: SubscriptionLevel[] = ["ban", "warn"];

function Subscriptions({ guildId }: { guildId: string }) {
  const subscriptions = useSubscriptions(guildId);
  const pools = usePools();
  const set = useSetSubscription(guildId);
  const remove = useDeleteSubscription(guildId);
  const notify = useToast();

  const [poolName, setPoolName] = useState("");
  const [level, setLevel] = useState<SubscriptionLevel>("ban");
  const [pending, setPending] = useState<{ poolName: string; revert: boolean } | null>(null);

  const subscribed = new Set(subscriptions.data?.map((entry) => entry.pool_name) ?? []);
  const available = pools.data?.filter((pool) => !subscribed.has(pool.name)) ?? [];

  return (
    <Card label="Subscriptions">
      <CardTitle>Subscriptions</CardTitle>
      <p className="mb-3 text-sm text-surface-muted">
        At <strong>ban</strong> level, everyone listed on the pool is banned here. At{" "}
        <strong>warn</strong> level nobody is banned — Timothy reports the match to your
        notification channel once and leaves them alone.
      </p>

      <ErrorNote error={subscriptions.error ?? set.error ?? remove.error} />
      {subscriptions.isPending ? <Loading what="subscriptions" /> : null}
      {subscriptions.data?.length === 0 ? (
        <Empty>This server enforces nothing.</Empty>
      ) : null}

      {subscriptions.data?.length ? (
        <Table head={["Pool", "Level", "Since", ""]}>
          {subscriptions.data.map((subscription) => (
            <Row key={subscription.pool_id}>
              <Cell className="font-medium">{subscription.pool_name}</Cell>
              <Cell>
                <Select
                  aria-label={`Level for ${subscription.pool_name}`}
                  value={subscription.level}
                  className="h-8 w-28"
                  onChange={(event) => {
                    const level = event.target.value as SubscriptionLevel;
                    set.mutate(
                      { poolName: subscription.pool_name, level },
                      {
                        onSuccess: () =>
                          notify(`${subscription.pool_name} set to ${level}.`),
                      },
                    );
                  }}
                >
                  {LEVELS.map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </Select>
              </Cell>
              <Cell>
                <When iso={subscription.created_at} />
              </Cell>
              <Cell className="text-right whitespace-nowrap">
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() =>
                    setPending({ poolName: subscription.pool_name, revert: false })
                  }
                >
                  Unsubscribe
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() =>
                    setPending({ poolName: subscription.pool_name, revert: true })
                  }
                >
                  Unsubscribe &amp; unban
                </Button>
              </Cell>
            </Row>
          ))}
        </Table>
      ) : null}

      {available.length ? (
        <form
          className="mt-4 flex flex-wrap items-end gap-2 border-t border-surface-border pt-4"
          onSubmit={(event) => {
            event.preventDefault();
            set.mutate(
              { poolName, level },
              {
                onSuccess: () => {
                  notify(`Subscribed to ${poolName}.`);
                  setPoolName("");
                },
              },
            );
          }}
        >
          <Field label="Subscribe to">
            <Select
              value={poolName}
              onChange={(event) => setPoolName(event.target.value)}
              required
              className="w-52"
            >
              <option value="">Choose a pool…</option>
              {available.map((pool) => (
                <option key={pool.id} value={pool.name}>
                  {pool.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Level">
            <Select
              value={level}
              onChange={(event) => setLevel(event.target.value as SubscriptionLevel)}
              className="w-28"
            >
              {LEVELS.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </Select>
          </Field>
          <Button type="submit" variant="primary" disabled={!poolName || set.isPending}>
            Subscribe
          </Button>
        </form>
      ) : null}

      <Confirm
        open={pending !== null}
        title={pending?.revert ? "Unsubscribe and lift the bans?" : "Unsubscribe?"}
        confirmLabel={pending?.revert ? "Unsubscribe and unban" : "Unsubscribe"}
        busy={remove.isPending}
        body={
          pending?.revert ? (
            <>
              <p>
                Timothy will unban everyone here that it can show it banned for this pool.
                Bans this server placed itself are never touched, and neither is anyone
                banned for a pool you are still subscribed to.
              </p>
              <p>This may be a large number of people.</p>
            </>
          ) : (
            <p>
              Nobody new will be banned for this pool. Everyone already banned stays
              banned.
            </p>
          )
        }
        onCancel={() => setPending(null)}
        onConfirm={() => {
          if (!pending) return;
          remove.mutate(pending, {
            onSuccess: () => {
              notify(
                pending.revert
                  ? `Unsubscribed from ${pending.poolName} and lifted its bans.`
                  : `Unsubscribed from ${pending.poolName}.`,
              );
              setPending(null);
            },
          });
        }}
      />
    </Card>
  );
}

// -- exceptions ------------------------------------------------------------------------

function Exceptions({ guildId }: { guildId: string }) {
  const exceptions = useExceptions(guildId);
  // The excepted, and whoever excepted them — except Timothy, which has no ID to name.
  const names = useUserNames([
    ...(exceptions.data ?? []).map((exception) => exception.user_id),
    ...actorIds((exceptions.data ?? []).map((exception) => exception.created_by)),
  ]);
  const create = useCreateException(guildId);
  const remove = useDeleteException(guildId);
  const notify = useToast();
  const [userId, setUserId] = useState("");
  const [reason, setReason] = useState("");

  return (
    <Card label="Exceptions">
      <CardTitle>Exceptions</CardTitle>
      <p className="mb-3 text-sm text-surface-muted">
        People Timothy will never ban here, whichever pool lists them. Server-wide, never
        for one pool.
      </p>

      <ErrorNote error={exceptions.error ?? create.error ?? remove.error} />
      {exceptions.isPending ? <Loading what="exceptions" /> : null}
      {exceptions.data?.length === 0 ? <Empty>Nobody is excepted here.</Empty> : null}

      {exceptions.data?.length ? (
        <Table head={["User", "Reason", "Added by", ""]}>
          {exceptions.data.map((exception) => (
            <Row key={exception.user_id}>
              <Cell>
                <UserName id={exception.user_id} name={names.data?.get(exception.user_id)} />
              </Cell>
              <Cell className="text-surface-muted">{exception.reason ?? "—"}</Cell>
              <Cell>
                <ActorRef actor={exception.created_by} names={names.data} />
              </Cell>
              <Cell className="text-right">
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={remove.isPending}
                  onClick={() =>
                    remove.mutate(exception.user_id, {
                      onSuccess: () => notify("Exception removed."),
                    })
                  }
                >
                  Remove
                </Button>
              </Cell>
            </Row>
          ))}
        </Table>
      ) : null}

      <form
        className="mt-4 space-y-2 border-t border-surface-border pt-4"
        onSubmit={(event) => {
          event.preventDefault();
          create.mutate(
            { userId, reason: reason || null },
            {
              onSuccess: () => {
                notify("Exception added.");
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
            className="font-mono"
          />
        </Field>
        <Field label="Reason">
          <Input
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="known to us, joined under a shared IP"
          />
        </Field>
        <Button type="submit" variant="primary" disabled={create.isPending}>
          Add exception
        </Button>
        <p className="text-xs text-surface-muted">
          Adding an exception does not unban anyone who is already banned.
        </p>
      </form>
    </Card>
  );
}

// -- notification channel --------------------------------------------------------------

function NotificationChannel({ guildId }: { guildId: string }) {
  const channel = useNotificationChannel(guildId);
  const set = useSetNotificationChannel(guildId);
  const clear = useClearNotificationChannel(guildId);
  const notify = useToast();
  const [channelId, setChannelId] = useState("");

  return (
    <Card label="Notification channel">
      <CardTitle>Notification channel</CardTitle>
      <p className="mb-3 text-sm text-surface-muted">
        Where Timothy reports what it did here — warn-level matches, bans it issued,
        exceptions it created. A warn subscription with no channel set reports nowhere.
      </p>

      <ErrorNote error={channel.error ?? set.error ?? clear.error} />
      {channel.isPending ? <Loading what="the channel" /> : null}

      {channel.data ? (
        <p className="mb-3 text-sm">
          Currently <Snowflake id={channel.data.channel_id} />{" "}
          <Button
            size="sm"
            variant="ghost"
            disabled={clear.isPending}
            onClick={() =>
              clear.mutate(undefined, {
                onSuccess: () => notify("Notification channel cleared."),
              })
            }
          >
            Clear
          </Button>
        </p>
      ) : (
        <Empty>No channel set.</Empty>
      )}

      <form
        className="space-y-2 border-t border-surface-border pt-4"
        onSubmit={(event) => {
          event.preventDefault();
          set.mutate(channelId, {
            onSuccess: () => {
              notify("Notification channel set.");
              setChannelId("");
            },
          });
        }}
      >
        <Field label="Channel ID">
          <Input
            value={channelId}
            onChange={(event) => setChannelId(event.target.value)}
            pattern="\d{1,20}"
            required
            className="font-mono"
            placeholder="400000000000000001"
          />
        </Field>
        <Button type="submit" variant="primary" disabled={set.isPending}>
          Set channel
        </Button>
      </form>
    </Card>
  );
}

// -- enforcement history ---------------------------------------------------------------

const STATUS_TONE = {
  banned: "ban",
  warned: "warn",
  failed: "ban",
  skipped_exception: "ok",
} as const;

const STATUS_LABEL = {
  banned: "banned",
  warned: "warned",
  failed: "failed",
  skipped_exception: "skipped — excepted",
} as const;

/**
 * What Timothy has actually done here.
 *
 * `enforcement_outcomes` read back. `failed` is the one worth filtering to: it is a ban
 * Timothy tried and could not issue — usually no permission, or a target that outranks
 * it — and the reason column says which.
 */
function History({ guildId }: { guildId: string }) {
  const [status, setStatus] = useState<OutcomeStatus | "">("");
  const outcomes = useEnforcement(guildId, status);
  const names = useUserNames((outcomes.data ?? []).map((outcome) => outcome.user_id));

  return (
    <Card label="Enforcement history">
      <CardTitle
        action={
          <Select
            aria-label="Filter by outcome"
            value={status}
            className="h-8 w-52"
            onChange={(event) => setStatus(event.target.value as OutcomeStatus | "")}
          >
            <option value="">Everything</option>
            <option value="banned">Banned</option>
            <option value="warned">Warned</option>
            <option value="failed">Failed</option>
            <option value="skipped_exception">Skipped — excepted</option>
          </Select>
        }
      >
        Enforcement history
      </CardTitle>

      <ErrorNote error={outcomes.error} />
      {outcomes.isPending ? <Loading what="history" /> : null}
      {outcomes.data?.length === 0 ? (
        <Empty>
          {status
            ? "Nothing with that outcome."
            : "Timothy has not enforced anything here yet."}
        </Empty>
      ) : null}

      {outcomes.data?.length ? (
        <Table head={["User", "Outcome", "Reason", "When"]}>
          {outcomes.data.map((outcome) => (
            <Row key={`${outcome.user_id}-${outcome.pool_id}`}>
              <Cell>
                <UserName id={outcome.user_id} name={names.data?.get(outcome.user_id)} />
              </Cell>
              <Cell>
                <Badge tone={STATUS_TONE[outcome.status]}>
                  {STATUS_LABEL[outcome.status]}
                </Badge>
              </Cell>
              <Cell className="text-surface-muted">{outcome.reason ?? "—"}</Cell>
              <Cell>
                <When iso={outcome.attempted_at} />
              </Cell>
            </Row>
          ))}
        </Table>
      ) : null}

      <p className="mt-3 text-xs text-surface-muted">
        A row here is Timothy's record that it acted itself, and it is what makes lifting
        a ban safe. Bans this server placed are not listed and are never touched.
      </p>
    </Card>
  );
}
