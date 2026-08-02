import {
  createRootRoute,
  createRoute,
  createRouter,
  type AnyRoute,
} from "@tanstack/react-router";

import { Shell } from "@/components/Shell";
import { AuditLog } from "@/routes/AuditLog";
import { GuildDetail } from "@/routes/GuildDetail";
import { Guilds } from "@/routes/Guilds";
import { Home } from "@/routes/Home";
import { Ops } from "@/routes/Ops";
import { PoolDetail } from "@/routes/PoolDetail";
import { Pools } from "@/routes/Pools";
import { UserLookup } from "@/routes/UserLookup";

/**
 * Routes, declared in code rather than generated from the filesystem.
 *
 * File-based routing needs the router's own Vite plugin and a generated tree checked in
 * beside the routes it was generated from. There are eight routes. Declaring them is
 * shorter than explaining the generated file, and it keeps the build to Vite, React and
 * Tailwind.
 *
 * Nothing here is a permission boundary. The navigation hides links a caller cannot use
 * and these routes are reachable by typing the URL — which is fine, because every one of
 * them renders whatever the API says, and the API resolves the permission itself
 * (ADR 0001). A screen someone should not see is a screen full of 403s, not a leak.
 */

const rootRoute = createRootRoute({ component: Shell });

const route = <T extends AnyRoute>(config: T): T => config;

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
  component: AuditLog,
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
]);

export function makeRouter() {
  return createRouter({ routeTree, defaultPreload: "intent" });
}

declare module "@tanstack/react-router" {
  interface Register {
    router: ReturnType<typeof makeRouter>;
  }
}
