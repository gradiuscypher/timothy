import { Link, useRouterState } from "@tanstack/react-router";

import { useMyGuilds, usePools, useSignedIn } from "@/api/hooks";
import { cn } from "@/components/cn";
import type { ReactNode } from "react";

/**
 * The rail beside a detail page: every other pool and server, one click away.
 *
 * A pool or a server is almost never looked at alone. The work is comparative — this
 * server's subscriptions against that one's, this pool's listings against the pool it is
 * about to be merged with — and doing that through the top bar means two navigations and
 * a list page in between, every time.
 *
 * It lives in the margin rather than in the page. Nothing here is worth narrowing a table
 * of user IDs for, so the rail only exists at the width where it costs the page nothing
 * (`--breakpoint-rail` in `styles.css` says exactly what that width is and why) and is
 * simply absent below it. That makes it a shortcut and never the only way through — the
 * top bar reaches the same two lists at every width.
 *
 * It is quiet about failure for the same reason. A rail that cannot load its pools shows
 * no pools; the page it sits beside is the thing the reader came for, and it reports its
 * own errors.
 */
export function ContextNav() {
  const session = useSignedIn();

  return (
    <aside className="hidden w-56 shrink-0 rail:block">
      {/* Sticky so it survives a long listing table, and scrollable in itself because a
          hundred-odd servers is longer than any viewport. */}
      <div className="sticky top-6 max-h-[calc(100vh-5rem)] space-y-5 overflow-y-auto">
        {/* `/pools` is 403 for anybody who does not manage pools, so the section that
            asks for it is not rendered rather than rendered empty. */}
        {session.data?.manages_pools ? <PoolRail /> : null}
        <GuildRail />
      </div>
    </aside>
  );
}

function RailSection({
  label,
  children,
  all,
}: {
  label: string;
  children: ReactNode;
  /** The link back to the whole list, kept last so the rail is never a dead end. */
  all: ReactNode;
}) {
  return (
    <nav aria-label={label}>
      <h2 className="px-2 pb-1.5 text-xs font-medium tracking-wide text-surface-muted uppercase">
        {label}
      </h2>
      <ul className="space-y-px">{children}</ul>
      <div className="mt-1">{all}</div>
    </nav>
  );
}

/**
 * The row and the footer styles, shared by both sections.
 *
 * A current row is marked with `aria-current="page"` as well as shading — the rail lists
 * the page the reader is already on, and that has to be announced and not merely seen.
 */
const railItem = (current: boolean) =>
  cn(
    "block truncate rounded-md px-2 py-1.5 text-sm",
    current
      ? "bg-surface-2 font-medium"
      : "text-surface-muted hover:bg-surface-2 hover:text-surface-ink",
  );

const RAIL_ALL =
  "block truncate rounded-md px-2 py-1 text-xs text-surface-muted " +
  "hover:bg-surface-2 hover:text-surface-ink";

/** What the address bar says, with `%20` and friends turned back into characters. */
function useDecodedPath() {
  const path = useRouterState({ select: (state) => state.location.pathname });
  try {
    return decodeURIComponent(path);
  } catch {
    // A malformed escape in a URL somebody typed is not worth throwing a whole rail for.
    return path;
  }
}

function PoolRail() {
  const pools = usePools();
  const path = useDecodedPath();

  if (!pools.data?.length) return null;

  return (
    <RailSection
      label="Pools"
      all={
        <Link to="/pools" className={RAIL_ALL}>
          All pools
        </Link>
      }
    >
      {pools.data.map((pool) => {
        const current = path === `/pools/${pool.name}`;
        return (
          <li key={pool.id}>
            <Link
              to="/pools/$name"
              params={{ name: pool.name }}
              title={pool.description ?? pool.name}
              aria-current={current ? "page" : undefined}
              className={railItem(current)}
            >
              {pool.name}
            </Link>
          </li>
        );
      })}
    </RailSection>
  );
}

/**
 * Servers by name where there is one, by ID where there is not — the same fallback the
 * list page makes, and for the same reason: the name is a cache the gateway fills.
 */
function GuildRail() {
  const guilds = useMyGuilds();
  const path = useDecodedPath();

  if (!guilds.data?.length) return null;

  return (
    <RailSection
      label="Servers"
      all={
        <Link to="/guilds" className={RAIL_ALL}>
          All servers
        </Link>
      }
    >
      {guilds.data.map((guild) => {
        const current = path === `/guilds/${guild.guild_id}`;
        return (
          <li key={guild.guild_id}>
            <Link
              to="/guilds/$guildId"
              params={{ guildId: guild.guild_id }}
              title={guild.name ?? guild.guild_id}
              aria-current={current ? "page" : undefined}
              className={cn(railItem(current), !guild.name && "snowflake")}
            >
              {guild.name ?? guild.guild_id}
            </Link>
          </li>
        );
      })}
    </RailSection>
  );
}
