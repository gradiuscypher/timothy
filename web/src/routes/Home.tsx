import { Link } from "@tanstack/react-router";

import { useMyGuilds, usePools, useSignedIn } from "@/api/hooks";
import { Badge, Card, CardTitle, Empty, GuildName, Loading, PageTitle } from "@/components/ui";

/**
 * What this person can do here, said plainly.
 *
 * Deliberately not a dashboard of counts. The two questions somebody arrives with are
 * "which of my servers is Timothy in" and "am I one of the people who owns pools", and
 * both are answered by what is on the screen rather than by a number.
 */
export function Home() {
  const me = useSignedIn();
  const guilds = useMyGuilds();

  return (
    <>
      <PageTitle>Timothy</PageTitle>

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardTitle>Your servers</CardTitle>
          <p className="mb-3 text-sm text-surface-muted">
            Servers Timothy is in that you administer. Subscriptions, exceptions and the
            notification channel are set per server.
          </p>
          {guilds.isPending ? <Loading what="your servers" /> : null}
          {guilds.data?.length === 0 ? (
            <Empty>
              You do not administer any server Timothy is in. If you have just been given
              administrator, sign out and back in.
            </Empty>
          ) : null}
          <ul className="space-y-1">
            {guilds.data?.map((guild) => (
              <li key={guild.guild_id}>
                <Link
                  to="/guilds/$guildId"
                  params={{ guildId: guild.guild_id }}
                  className="flex items-center justify-between rounded-md px-2 py-1.5 hover:bg-surface-2"
                >
                  <GuildName id={guild.guild_id} name={guild.name} />
                  {guild.enforcement_paused ? <Badge tone="warn">paused</Badge> : null}
                </Link>
              </li>
            ))}
          </ul>
        </Card>

        <Card>
          <CardTitle>Pools</CardTitle>
          {/* Mounted only for somebody who owns pools, so the fetch happens only for
              somebody the list is for. */}
          {me.data?.manages_pools ? <PoolList /> : <NotYourPools />}
        </Card>
      </div>
    </>
  );
}

function PoolList() {
  const pools = usePools();

  return (
    <>
      <p className="mb-3 text-sm text-surface-muted">
        You administer the management server, so you own the pools and everything listed
        on them.
      </p>
      {pools.isPending ? <Loading what="pools" /> : null}
      <ul className="space-y-1">
        {pools.data?.map((pool) => (
          <li key={pool.id}>
            <Link
              to="/pools/$name"
              params={{ name: pool.name }}
              className="block rounded-md px-2 py-1.5 hover:bg-surface-2"
            >
              <span className="font-medium">{pool.name}</span>
              {pool.description ? (
                <span className="ml-2 text-sm text-surface-muted">{pool.description}</span>
              ) : null}
            </Link>
          </li>
        ))}
      </ul>
    </>
  );
}

function NotYourPools() {
  return (
    <p className="text-sm text-surface-muted">
      Pools are owned by the administrators of the management server. You can still{" "}
      <Link to="/users" className="text-accent underline">
        look up why a user is listed
      </Link>
      , and subscribe your own servers to a pool from the server page.
    </p>
  );
}
