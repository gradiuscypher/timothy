import { Link } from "@tanstack/react-router";
import { useState } from "react";

import { useGuildConfig, useGuildConfigs } from "@/api/hooks";
import {
  ActorRef,
  Badge,
  Banner,
  Card,
  CardTitle,
  Cell,
  Empty,
  ErrorNote,
  Field,
  FilterBar,
  GuildName,
  Input,
  Loading,
  PageTitle,
  Row,
  Snowflake,
  Table,
  When,
} from "@/components/ui";

/**
 * Every server's settings, for the person who administers none of them.
 *
 * `Guilds` and `GuildDetail` are the administrator's pair: they show the servers the
 * signed-in person runs, and they are where the settings get *changed*. These two are
 * the operator's, and the difference is the whole point — the report is "Timothy has
 * stopped banning in my server", the operator is not in that server, and the answer is
 * usually visible in one screen of its configuration (a pause, a warn-level
 * subscription, an exception somebody added and forgot).
 *
 * Read-only, deliberately and permanently. Seeing a setting in order to explain it is
 * not authority over it: a subscription belongs to the guild that holds it, and an
 * operator who quietly fixed one would leave its administrators with a configuration
 * they did not set and cannot account for. What this page is for is being able to say
 * *which* setting to change, to the person whose setting it is.
 */
export function OpsGuilds() {
  const [q, setQ] = useState("");
  const configs = useGuildConfigs(q);

  return (
    <>
      <PageTitle>Servers</PageTitle>

      <FilterBar
        label="Find a server"
        onClear={q ? () => setQ("") : undefined}
      >
        <Field
          label="Search"
          hint="A server's name, or its ID from a log line."
          className="min-w-64 grow"
        >
          <Input
            type="search"
            value={q}
            placeholder="Any name or ID"
            onChange={(event) => setQ(event.target.value)}
          />
        </Field>
      </FilterBar>

      <Card label="Servers">
        <ErrorNote error={configs.error} />
        {configs.isPending ? <Loading what="servers" /> : null}
        {configs.data?.length === 0 ? (
          <Empty>
            {q ? "No server matches that." : "Timothy is not in any server yet."}
          </Empty>
        ) : null}

        {configs.data?.length ? (
          <Table
            head={[
              "Server",
              "Enforcement",
              "Subscriptions",
              "Exceptions",
              "Notifications",
              "Timothy joined",
            ]}
          >
            {configs.data.map((config) => (
              <Row key={config.guild_id}>
                <Cell>
                  <Link
                    to="/ops/guilds/$guildId"
                    params={{ guildId: config.guild_id }}
                    className="text-accent hover:underline"
                  >
                    <GuildName id={config.guild_id} name={config.name} />
                  </Link>
                </Cell>
                <Cell>
                  {config.enforcement_paused ? (
                    <Badge tone="warn">paused</Badge>
                  ) : (
                    <Badge tone="ok">active</Badge>
                  )}
                </Cell>
                {/* Ban and warn apart, because a server that believes it is enforcing
                    and is subscribed at warn looks exactly like one that is working —
                    right up until nobody is banned. */}
                <Cell className="tabular-nums">
                  {config.ban_subscriptions + config.warn_subscriptions === 0 ? (
                    <span className="text-surface-muted">none</span>
                  ) : (
                    <span className="flex flex-wrap gap-1">
                      {config.ban_subscriptions ? (
                        <Badge tone="ban">{config.ban_subscriptions} ban</Badge>
                      ) : null}
                      {config.warn_subscriptions ? (
                        <Badge tone="warn">{config.warn_subscriptions} warn</Badge>
                      ) : null}
                    </span>
                  )}
                </Cell>
                <Cell className="tabular-nums">{config.exceptions}</Cell>
                <Cell>
                  {config.notification_channel_id ? (
                    <Snowflake id={config.notification_channel_id} className="text-xs" />
                  ) : (
                    <span className="text-surface-muted">none</span>
                  )}
                </Cell>
                <Cell className="whitespace-nowrap">
                  <When iso={config.joined_at} />
                </Cell>
              </Row>
            ))}
          </Table>
        ) : null}

        <p className="mt-3 text-xs text-surface-muted">
          Every server Timothy is in, not only the ones you administer. Reading only —
          settings are changed by each server&apos;s own administrators.
        </p>
      </Card>
    </>
  );
}

/**
 * One server's settings in full, from the outside.
 *
 * The same four things `GuildDetail` shows an administrator, minus everything that is a
 * button. What is deliberately not here is what Timothy has *done* in this server and
 * what Discord will *let* it do: enforcement history and ban readiness are their own
 * questions with their own audiences, and the operations overview already reports the
 * failures across every server at once.
 */
export function OpsGuild({ guildId }: { guildId: string }) {
  const config = useGuildConfig(guildId);

  if (config.isPending) return <Loading what="this server" />;
  if (config.isError) return <ErrorNote error={config.error} />;

  const { guild, subscriptions, exceptions, notification_channel: channel } = config.data;

  return (
    <>
      <PageTitle
        action={
          guild.enforcement_paused ? (
            <Badge tone="warn">enforcement paused</Badge>
          ) : (
            <Badge tone="ok">enforcement active</Badge>
          )
        }
      >
        <GuildName id={guild.guild_id} name={guild.name} />
      </PageTitle>

      {guild.enforcement_paused ? (
        <Banner tone="warn" className="mb-4">
          Enforcement is paused here. Timothy is recording nothing and issuing nothing in
          this server until an administrator resumes it — which is the everyday answer to
          &ldquo;Timothy has stopped banning in my server&rdquo;.
        </Banner>
      ) : null}

      <div className="space-y-4">
        <Card label="Subscriptions">
          <CardTitle>Subscriptions</CardTitle>
          {subscriptions.length === 0 ? (
            <Empty>
              This server subscribes to nothing, so Timothy enforces nothing in it.
            </Empty>
          ) : (
            <Table head={["Pool", "Level", "Set by", "Since"]}>
              {subscriptions.map((subscription) => (
                <Row key={subscription.pool_id}>
                  <Cell>
                    <Link
                      to="/pools/$name"
                      params={{ name: subscription.pool_name }}
                      className="text-accent hover:underline"
                    >
                      {subscription.pool_name}
                    </Link>
                  </Cell>
                  <Cell>
                    <Badge tone={subscription.level === "ban" ? "ban" : "warn"}>
                      {subscription.level}
                    </Badge>
                  </Cell>
                  <Cell>
                    <ActorRef actor={subscription.created_by} />
                  </Cell>
                  <Cell className="whitespace-nowrap">
                    <When iso={subscription.created_at} />
                  </Cell>
                </Row>
              ))}
            </Table>
          )}
          <p className="mt-3 text-xs text-surface-muted">
            A warn-level subscription never bans anybody. It reports to the notification
            channel below, and a server that has nominated none reports nowhere.
          </p>
        </Card>

        <div className="grid gap-4 lg:grid-cols-2">
          <Card label="Exceptions">
            <CardTitle>Exceptions</CardTitle>
            {exceptions.length === 0 ? (
              <Empty>Nobody is excepted here.</Empty>
            ) : (
              <Table head={["User", "Reason", "Added by", "When"]}>
                {exceptions.map((exception) => (
                  <Row key={exception.user_id}>
                    <Cell>
                      <Link
                        to="/users/$userId"
                        params={{ userId: exception.user_id }}
                        className="text-accent hover:underline"
                      >
                        <Snowflake id={exception.user_id} />
                      </Link>
                    </Cell>
                    <Cell className="text-surface-muted">{exception.reason ?? "—"}</Cell>
                    <Cell>
                      <ActorRef actor={exception.created_by} />
                    </Cell>
                    <Cell className="whitespace-nowrap">
                      <When iso={exception.created_at} />
                    </Cell>
                  </Row>
                ))}
              </Table>
            )}
            <p className="mt-3 text-xs text-surface-muted">
              An exception is server-wide: this user is never banned here, whichever pool
              lists them.
            </p>
          </Card>

          <Card label="Notification channel">
            <CardTitle>Notification channel</CardTitle>
            {channel === null ? (
              <Empty>
                No channel is nominated, so Timothy reports nothing back into this server.
              </Empty>
            ) : (
              <Table head={["Channel", "Set by", "Since"]}>
                <Row>
                  <Cell>
                    <Snowflake id={channel.channel_id} />
                  </Cell>
                  <Cell>
                    <ActorRef actor={channel.created_by} />
                  </Cell>
                  <Cell className="whitespace-nowrap">
                    <When iso={channel.created_at} />
                  </Cell>
                </Row>
              </Table>
            )}
          </Card>
        </div>
      </div>

      <p className="mt-4 text-xs text-surface-muted">
        Reading only. These settings belong to this server&apos;s own administrators, and
        changing one from here would leave them with a configuration they did not set.
      </p>
    </>
  );
}
