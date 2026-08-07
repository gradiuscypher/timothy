import {
  createRootRoute,
  createRoute,
  createRouter,
  type AnyRoute,
} from "@tanstack/react-router";

import { Shell, type Layout } from "@/components/Shell";
import { AuditLog } from "@/routes/AuditLog";
import { GuildDetail } from "@/routes/GuildDetail";
import { Guilds } from "@/routes/Guilds";
import { Home } from "@/routes/Home";
import { Jobs } from "@/routes/Jobs";
import { Ops } from "@/routes/Ops";
import { OpsGuild, OpsGuilds } from "@/routes/OpsGuilds";
import { PoolDetail } from "@/routes/PoolDetail";
import { Pools } from "@/routes/Pools";
import { UserLookup } from "@/routes/UserLookup";

/**
 * Routes, declared in code rather than generated from the filesystem.
 *
 * File-based routing needs the router's own Vite plugin and a generated tree checked in
 * beside the routes it was generated from. There are a dozen routes. Declaring them is
 * shorter than explaining the generated file, and it keeps the build to Vite, React and
 * Tailwind.
 *
 * Each route also says how wide it wants to be, in `staticData.layout` — `Shell` reads it
 * off the deepest match and shapes the page around it. It lives here rather than in the
 * route components because it is a property of the *frame*, and because a list of paths
 * beside a list of shapes is the only place the two can be seen to agree.
 *
 * Nothing here is a permission boundary. The navigation hides links a caller cannot use
 * and these routes are reachable by typing the URL — which is fine, because every one of
 * them renders whatever the API says, and the API resolves the permission itself
 * (ADR 0001). A screen someone should not see is a screen full of 403s, not a leak.
 */

const rootRoute = createRootRoute({ component: Shell });

const route = <T extends AnyRoute>(config: T): T => config;

/** Routes that say nothing get the centred column. */
declare module "@tanstack/react-router" {
  interface StaticDataRouteOption {
    layout?: Layout;
  }
}

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  component: Home,
});

const poolsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/pools",
  component: Pools,
});

const poolRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/pools/$name",
  // A pool is rarely read alone — the rail puts its siblings a click away.
  staticData: { layout: "rail" },
  component: function PoolRoute() {
    const { name } = poolRoute.useParams();
    // Remounting on a rename keeps the settings form's own state from surviving into a
    // pool it no longer describes.
    return <PoolDetail key={name} name={name} />;
  },
});

const guildsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/guilds",
  component: Guilds,
});

const guildRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/guilds/$guildId",
  staticData: { layout: "rail" },
  component: function GuildRoute() {
    const { guildId } = guildRoute.useParams();
    return <GuildDetail key={guildId} guildId={guildId} />;
  },
});

const usersRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/users",
  component: function UsersRoute() {
    return <UserLookup />;
  },
});

const userRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/users/$userId",
  component: function UserRoute() {
    const { userId } = userRoute.useParams();
    return <UserLookup key={userId} userId={userId} />;
  },
});

const opsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/ops",
  component: Ops,
});

const auditRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/audit",
  // Five columns, one of them a JSON blob. It gets the window.
  staticData: { layout: "wide" },
  component: AuditLog,
});

const opsGuildsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/ops/guilds",
  // Six columns of configuration across every server Timothy is in.
  staticData: { layout: "wide" },
  component: OpsGuilds,
});

const opsGuildRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/ops/guilds/$guildId",
  component: function OpsGuildRoute() {
    const { guildId } = opsGuildRoute.useParams();
    return <OpsGuild key={guildId} guildId={guildId} />;
  },
});

const jobsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/ops/jobs",
  staticData: { layout: "wide" },
  component: Jobs,
});

export const routeTree = rootRoute.addChildren([
  route(indexRoute),
  route(poolsRoute),
  route(poolRoute),
  route(guildsRoute),
  route(guildRoute),
  route(usersRoute),
  route(userRoute),
  route(auditRoute),
  route(opsRoute),
  route(jobsRoute),
  route(opsGuildsRoute),
  route(opsGuildRoute),
]);

export function makeRouter() {
  return createRouter({ routeTree, defaultPreload: "intent" });
}

declare module "@tanstack/react-router" {
  interface Register {
    router: ReturnType<typeof makeRouter>;
  }
}
