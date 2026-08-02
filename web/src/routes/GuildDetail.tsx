import { useState } from "react";

import type { OutcomeStatus, SubscriptionLevel } from "@/api/client";
import {
  useClearNotificationChannel,
  useCreateException,
  useDeleteException,
  useDeleteSubscription,
  useEnforcement,
  useExceptions,
  useGuild,
  useNotificationChannel,
  usePauseEnforcement,
  usePools,
  useSetNotificationChannel,
  useSetSubscription,
  useSubscriptions,
} from "@/api/hooks";
import {
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
  Select,
  Snowflake,
  Table,
  When,
} from "@/components/ui";

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
        <span className="snowflake text-lg">{guildId}</span>
      </PageTitle>

      {guild.data.enforcement_paused ? (
        <p className="mb-4 rounded-md bg-warn/10 px-3 py-2 text-sm text-warn" role="status">
          Enforcement is paused here. Timothy is recording nothing and issuing nothing in
          this server. Resuming queues a catch-up over everything missed.
        </p>
      ) : null}

      <div className="space-y-4">
        <Subscriptions guildId={guildId} />
        <div className="grid gap-4 lg:grid-cols-2">
          <Exceptions guildId={guildId} />
          <NotificationChannel guildId={guildId} />
        </div>
        <History guildId={guildId} />
      </div>
    </>
  );
}

function PauseSwitch({ guildId, paused }: { guildId: string; paused: boolean }) {
  const pause = usePauseEnforcement(guildId);
  return (
    <div className="flex items-center gap-2">
      <ErrorNote error={pause.error} />
      <Button
        variant={paused ? "primary" : "secondary"}
        disabled={pause.isPending}
        onClick={() => pause.mutate(!paused)}
      >
        {paused ? "Resume enforcement" : "Pause enforcement"}
      </Button>
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
                  onChange={(event) =>
                    set.mutate({
                      poolName: subscription.pool_name,
                      level: event.target.value as SubscriptionLevel,
                    })
                  }
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
            set.mutate({ poolName, level }, { onSuccess: () => setPoolName("") });
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
          remove.mutate(pending, { onSuccess: () => setPending(null) });
        }}
      />
    </Card>
  );
}

// -- exceptions ------------------------------------------------------------------------

function Exceptions({ guildId }: { guildId: string }) {
  const exceptions = useExceptions(guildId);
  const create = useCreateException(guildId);
  const remove = useDeleteException(guildId);
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
                <Snowflake id={exception.user_id} />
              </Cell>
              <Cell className="text-surface-muted">{exception.reason ?? "—"}</Cell>
              <Cell>
                {exception.created_by === "system" ? (
                  <Badge>Timothy</Badge>
                ) : (
                  <Snowflake id={exception.created_by.replace("user:", "")} />
                )}
              </Cell>
              <Cell className="text-right">
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={remove.isPending}
                  onClick={() => remove.mutate(exception.user_id)}
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
            onClick={() => clear.mutate()}
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
          set.mutate(channelId, { onSuccess: () => setChannelId("") });
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
                <Snowflake id={outcome.user_id} />
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
