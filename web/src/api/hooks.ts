import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";

import {
  api,
  ApiError,
  unwrap,
  type AuditEntry,
  type BanFailure,
  type BanFailureDiagnosis,
  type BulkResult,
  type Guild,
  type GuildDiagnostics,
  type GuildException,
  type Listing,
  type ListingPage,
  type NotificationChannel,
  type Outcome,
  type OutcomeStatus,
  type Pool,
  type SignedIn,
  type Subscription,
  type ActivityPoint,
  type FailureGroup,
  type GuildConfig,
  type GuildConfigSummary,
  type Job,
  type JobStatus,
  type OpsOverview,
  type SubscriptionLevel,
} from "./client";

/**
 * Every call the UI makes, as a hook.
 *
 * The rule this file follows: **a mutation invalidates what it could have changed, and
 * nothing else.** Timothy's mutations are not local — deleting a listing with `revert`
 * enqueues unbans across every subscribing guild — so the screens showing consequences
 * are refetched rather than patched. Guessing the new state locally would show an
 * outcome that has not happened yet.
 */

export const keys = {
  me: ["me"] as const,
  pools: ["pools"] as const,
  pool: (name: string) => ["pools", name] as const,
  listings: (name: string, q: string) => ["pools", name, "listings", q] as const,
  userListings: (userId: string) => ["users", userId, "listings"] as const,
  userNames: (ids: string[]) => ["users", "names", ids.join(",")] as const,
  guilds: ["guilds"] as const,
  guild: (id: string) => ["guilds", id] as const,
  subscriptions: (id: string) => ["guilds", id, "subscriptions"] as const,
  exceptions: (id: string) => ["guilds", id, "exceptions"] as const,
  channel: (id: string) => ["guilds", id, "notification-channel"] as const,
  enforcement: (id: string, status: string) => ["guilds", id, "enforcement", status] as const,
  diagnostics: (id: string) => ["guilds", id, "diagnostics"] as const,
  banFailures: (id: string) => ["guilds", id, "diagnostics", "failures"] as const,
  banFailure: (id: string, userId: string) =>
    ["guilds", id, "diagnostics", "failures", userId] as const,
  auditLog: (action: string, q: string) => ["audit-log", action, q] as const,
  opsOverview: (days: number) => ["ops", "overview", days] as const,
  opsActivity: (days: number) => ["ops", "activity", days] as const,
  opsFailures: ["ops", "failures"] as const,
  opsJobs: (status: string, kind: string, q: string) =>
    ["ops", "jobs", status, kind, q] as const,
  opsGuilds: (q: string) => ["ops", "guilds", q] as const,
  opsGuild: (id: string) => ["ops", "guilds", id] as const,
};

// -- who is signed in ------------------------------------------------------------------

/**
 * The current session, or `null` when there is not one.
 *
 * A 401 is an *answer* here rather than a failure — it is how a browser that has never
 * logged in finds out — so it resolves to `null` and the app shows the login screen.
 * Every other error is left to throw.
 */
export function useSignedIn(): UseQueryResult<SignedIn | null> {
  return useQuery({
    queryKey: keys.me,
    retry: false,
    staleTime: 60_000,
    queryFn: async () => {
      try {
        return unwrap(await api.GET("/auth/me"));
      } catch (error) {
        if (error instanceof ApiError && error.isUnauthenticated) return null;
        throw error;
      }
    },
  });
}

export function useLogout(): UseMutationResult<void, Error, void> {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      await api.POST("/auth/logout");
    },
    // Everything on every screen was answered for one person. None of it survives.
    onSuccess: () => cache.clear(),
  });
}

// -- pools -----------------------------------------------------------------------------

export function usePools(): UseQueryResult<Pool[]> {
  return useQuery({
    queryKey: keys.pools,
    queryFn: async () => unwrap(await api.GET("/pools")),
  });
}

export function usePool(name: string): UseQueryResult<Pool> {
  return useQuery({
    queryKey: keys.pool(name),
    queryFn: async () =>
      unwrap(await api.GET("/pools/{name}", { params: { path: { name } } })),
  });
}

export function useCreatePool(): UseMutationResult<
  Pool,
  Error,
  { name: string; description: string | null }
> {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: async (body) => unwrap(await api.POST("/pools", { body })),
    onSuccess: () => cache.invalidateQueries({ queryKey: keys.pools }),
  });
}

export function useUpdatePool(
  name: string,
): UseMutationResult<Pool, Error, { name?: string | null; description?: string | null }> {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: async (body) =>
      unwrap(await api.PATCH("/pools/{name}", { params: { path: { name } }, body })),
    // A rename changes the key every subscription screen shows it under.
    onSuccess: () => cache.invalidateQueries(),
  });
}

export function useDeletePool(): UseMutationResult<
  void,
  Error,
  { name: string; revert: boolean }
> {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: async ({ name, revert }) => {
      await api.DELETE("/pools/{name}", { params: { path: { name }, query: { revert } } });
    },
    // Its listings and every subscription to it went with it, and `revert` may be
    // lifting bans in guilds this screen never named.
    onSuccess: () => cache.invalidateQueries(),
  });
}

// -- listings --------------------------------------------------------------------------

export function useListings(
  name: string,
  options: { q: string; limit: number; afterId: number | null },
): UseQueryResult<ListingPage> {
  const { q, limit, afterId } = options;
  return useQuery({
    queryKey: [...keys.listings(name, q), limit, afterId],
    // Keeps the table on screen while the next page loads instead of blanking it, which
    // at 50 rows a page is the difference between paging and flickering.
    placeholderData: (previous) => previous,
    queryFn: async () =>
      unwrap(
        await api.GET("/pools/{name}/listings", {
          params: {
            path: { name },
            query: {
              limit,
              ...(q ? { q } : {}),
              ...(afterId === null ? {} : { after_id: afterId }),
            },
          },
        }),
      ),
  });
}

export function useUserListings(userId: string): UseQueryResult<Listing[]> {
  return useQuery({
    queryKey: keys.userListings(userId),
    enabled: userId.length > 0,
    queryFn: async () =>
      unwrap(
        await api.GET("/users/{user_id}/listings", {
          params: { path: { user_id: userId } },
        }),
      ),
  });
}

/**
 * The last known name for each of these IDs, as a lookup keyed by ID.
 *
 * Every screen here is a list of snowflakes, and a snowflake names nobody. A page hands
 * over the IDs it is about to draw and gets back the ones Timothy has ever seen a name
 * for; the rest are absent, and `<UserName>` shows the ID alone for those. That is why
 * the result is a `Map` rather than a record of `string | null` — there is no value
 * meaning "never seen", only the absence of one.
 *
 * Sorted into the query key so that the same set of IDs in a different render order is
 * the same query, and one page's rows are fetched once rather than per re-order.
 */
export function useUserNames(userIds: string[]): UseQueryResult<Map<string, string>> {
  const ids = [...new Set(userIds)].sort();
  return useQuery({
    queryKey: keys.userNames(ids),
    enabled: ids.length > 0,
    // Names change about as often as people rename themselves, which is far less often
    // than a page is drawn. Nothing here is a decision, so a slightly old one is fine.
    staleTime: 300_000,
    queryFn: async () => {
      const names = unwrap(await api.GET("/users/names", { params: { query: { id: ids } } }));
      return new Map(names.map((known) => [known.user_id, known.name]));
    },
  });
}

export function useCreateListing(
  name: string,
): UseMutationResult<Listing, Error, { user_id: string; reason: string }> {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: async (body) =>
      unwrap(
        await api.POST("/pools/{name}/listings", { params: { path: { name } }, body }),
      ),
    onSuccess: () => cache.invalidateQueries({ queryKey: keys.pool(name) }),
  });
}

export function useDeleteListing(
  name: string,
): UseMutationResult<void, Error, { userId: string; revert: boolean }> {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: async ({ userId, revert }) => {
      await api.DELETE("/pools/{name}/listings/{user_id}", {
        params: { path: { name, user_id: userId }, query: { revert } },
      });
    },
    onSuccess: () => cache.invalidateQueries(),
  });
}

export function useBulkListings(
  name: string,
): UseMutationResult<BulkResult, Error, { reason: string; user_ids: string[] }> {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: async (body) =>
      unwrap(
        await api.POST("/pools/{name}/listings/bulk", {
          params: { path: { name } },
          body,
        }),
      ),
    onSuccess: () => cache.invalidateQueries({ queryKey: keys.pool(name) }),
  });
}

export function useBulkDeleteListings(
  name: string,
): UseMutationResult<BulkResult, Error, { user_ids: string[]; revert: boolean }> {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: async ({ user_ids, revert }) =>
      unwrap(
        await api.POST("/pools/{name}/listings/bulk-delete", {
          params: { path: { name }, query: { revert } },
          body: { user_ids },
        }),
      ),
    onSuccess: () => cache.invalidateQueries(),
  });
}

// -- guilds ----------------------------------------------------------------------------

export function useMyGuilds(): UseQueryResult<Guild[]> {
  return useQuery({
    queryKey: keys.guilds,
    queryFn: async () => unwrap(await api.GET("/guilds")),
  });
}

export function useGuild(guildId: string): UseQueryResult<Guild> {
  return useQuery({
    queryKey: keys.guild(guildId),
    queryFn: async () =>
      unwrap(await api.GET("/guilds/{guild_id}", { params: { path: { guild_id: guildId } } })),
  });
}

export function usePauseEnforcement(
  guildId: string,
): UseMutationResult<Guild, Error, boolean> {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: async (paused) =>
      unwrap(
        await api.PATCH("/guilds/{guild_id}", {
          params: { path: { guild_id: guildId } },
          body: { enforcement_paused: paused },
        }),
      ),
    // Resuming enqueues a catch-up, so the enforcement history is about to move.
    onSuccess: () => cache.invalidateQueries({ queryKey: keys.guild(guildId) }),
  });
}

// -- subscriptions ---------------------------------------------------------------------

export function useSubscriptions(guildId: string): UseQueryResult<Subscription[]> {
  return useQuery({
    queryKey: keys.subscriptions(guildId),
    queryFn: async () =>
      unwrap(
        await api.GET("/guilds/{guild_id}/subscriptions", {
          params: { path: { guild_id: guildId } },
        }),
      ),
  });
}

export function useSetSubscription(
  guildId: string,
): UseMutationResult<Subscription, Error, { poolName: string; level: SubscriptionLevel }> {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: async ({ poolName, level }) =>
      unwrap(
        await api.PUT("/guilds/{guild_id}/subscriptions/{pool_name}", {
          params: { path: { guild_id: guildId, pool_name: poolName } },
          body: { level },
        }),
      ),
    onSuccess: () => cache.invalidateQueries({ queryKey: keys.guild(guildId) }),
  });
}

export function useDeleteSubscription(
  guildId: string,
): UseMutationResult<void, Error, { poolName: string; revert: boolean }> {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: async ({ poolName, revert }) => {
      await api.DELETE("/guilds/{guild_id}/subscriptions/{pool_name}", {
        params: { path: { guild_id: guildId, pool_name: poolName }, query: { revert } },
      });
    },
    onSuccess: () => cache.invalidateQueries({ queryKey: keys.guild(guildId) }),
  });
}

// -- exceptions ------------------------------------------------------------------------

export function useExceptions(guildId: string): UseQueryResult<GuildException[]> {
  return useQuery({
    queryKey: keys.exceptions(guildId),
    queryFn: async () =>
      unwrap(
        await api.GET("/guilds/{guild_id}/exceptions", {
          params: { path: { guild_id: guildId } },
        }),
      ),
  });
}

export function useCreateException(
  guildId: string,
): UseMutationResult<GuildException, Error, { userId: string; reason: string | null }> {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: async ({ userId, reason }) =>
      unwrap(
        await api.PUT("/guilds/{guild_id}/exceptions/{user_id}", {
          params: { path: { guild_id: guildId, user_id: userId } },
          body: { reason },
        }),
      ),
    onSuccess: () => cache.invalidateQueries({ queryKey: keys.guild(guildId) }),
  });
}

export function useDeleteException(
  guildId: string,
): UseMutationResult<void, Error, string> {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: async (userId) => {
      await api.DELETE("/guilds/{guild_id}/exceptions/{user_id}", {
        params: { path: { guild_id: guildId, user_id: userId } },
      });
    },
    onSuccess: () => cache.invalidateQueries({ queryKey: keys.guild(guildId) }),
  });
}

// -- notification channel --------------------------------------------------------------

/** `null` when the guild has not set one — a 404 is the answer, not a failure. */
export function useNotificationChannel(
  guildId: string,
): UseQueryResult<NotificationChannel | null> {
  return useQuery({
    queryKey: keys.channel(guildId),
    queryFn: async () => {
      try {
        return unwrap(
          await api.GET("/guilds/{guild_id}/notification-channel", {
            params: { path: { guild_id: guildId } },
          }),
        );
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      }
    },
  });
}

export function useSetNotificationChannel(
  guildId: string,
): UseMutationResult<NotificationChannel, Error, string> {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: async (channelId) =>
      unwrap(
        await api.PUT("/guilds/{guild_id}/notification-channel", {
          params: { path: { guild_id: guildId } },
          body: { channel_id: channelId },
        }),
      ),
    onSuccess: () => cache.invalidateQueries({ queryKey: keys.channel(guildId) }),
  });
}

export function useClearNotificationChannel(
  guildId: string,
): UseMutationResult<void, Error, void> {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      await api.DELETE("/guilds/{guild_id}/notification-channel", {
        params: { path: { guild_id: guildId } },
      });
    },
    onSuccess: () => cache.invalidateQueries({ queryKey: keys.channel(guildId) }),
  });
}

// -- what Timothy has done -------------------------------------------------------------

export function useEnforcement(
  guildId: string,
  status: OutcomeStatus | "",
): UseQueryResult<Outcome[]> {
  return useQuery({
    queryKey: keys.enforcement(guildId, status),
    queryFn: async () =>
      unwrap(
        await api.GET("/guilds/{guild_id}/enforcement", {
          params: {
            path: { guild_id: guildId },
            query: status ? { status } : {},
          },
        }),
      ),
  });
}

// -- can Timothy do its job here -------------------------------------------------------

/**
 * How long a page waits to notice a snapshot the bot has just pushed.
 *
 * The refresh button cannot reach the bot — it records a request the bot collects on its
 * own poll (ADR 0016) — so there is nothing to await and the only way to see the answer
 * arrive is to keep asking. Fifteen seconds is under the bot's own poll interval, so the
 * new snapshot appears within a tick of landing, and each ask is one indexed row.
 */
const DIAGNOSTICS_REFRESH_MS = 15_000;

/**
 * Whether Timothy can ban in this guild, and what it can never reach.
 *
 * `null` when the bot has never reported — a 404 is the answer, as it is for the
 * notification channel. Deliberately not defaulted to an all-clear: a guild nothing has
 * looked at is not a guild where everything is fine, and the two must not render alike.
 */
export function useGuildDiagnostics(
  guildId: string,
): UseQueryResult<GuildDiagnostics | null> {
  return useQuery({
    queryKey: keys.diagnostics(guildId),
    refetchInterval: DIAGNOSTICS_REFRESH_MS,
    queryFn: async () => {
      try {
        return unwrap(
          await api.GET("/guilds/{guild_id}/diagnostics", {
            params: { path: { guild_id: guildId } },
          }),
        );
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      }
    },
  });
}

/**
 * Ask for this guild to be looked at again, out of turn.
 *
 * Nothing to invalidate on success: the backend has only *recorded* the request, and the
 * snapshot changes when the bot gets round to it. The poll above is what shows the answer.
 */
export function useRefreshDiagnostics(
  guildId: string,
): UseMutationResult<void, Error, void> {
  return useMutation({
    mutationFn: async () => {
      await api.POST("/guilds/{guild_id}/diagnostics/refresh", {
        params: { path: { guild_id: guildId } },
      });
    },
  });
}

export function useBanFailures(guildId: string): UseQueryResult<BanFailure[]> {
  return useQuery({
    queryKey: keys.banFailures(guildId),
    queryFn: async () =>
      unwrap(
        await api.GET("/guilds/{guild_id}/diagnostics/failures", {
          params: { path: { guild_id: guildId } },
        }),
      ),
  });
}

/**
 * Why one ban failed, resolved against Discord now.
 *
 * This is the only call on the guild screen that costs a Discord lookup, so it is gated
 * by where it is used rather than by an `enabled` flag: the component that calls it is
 * mounted when somebody opens a row and unmounted when they close it.
 */
export function useBanFailureDiagnosis(
  guildId: string,
  userId: string,
): UseQueryResult<BanFailureDiagnosis> {
  return useQuery({
    queryKey: keys.banFailure(guildId, userId),
    queryFn: async () =>
      unwrap(
        await api.GET("/guilds/{guild_id}/diagnostics/failures/{user_id}", {
          params: { path: { guild_id: guildId, user_id: userId } },
        }),
      ),
  });
}

export function useAuditLog(
  filters: { action: string; q: string },
  beforeId: number | null,
): UseQueryResult<AuditEntry[]> {
  const { action, q } = filters;
  return useQuery({
    queryKey: [...keys.auditLog(action, q), beforeId],
    placeholderData: (previous) => previous,
    queryFn: async () =>
      unwrap(
        await api.GET("/audit-log", {
          params: {
            query: {
              limit: 50,
              ...(action ? { action } : {}),
              ...(q ? { q } : {}),
              ...(beforeId === null ? {} : { before_id: beforeId }),
            },
          },
        }),
      ),
  });
}

// -- is this thing working -------------------------------------------------------------

/**
 * The operator's view refetches on an interval rather than on demand, because the
 * question it answers is "what is happening *now*" and a stale queue depth is worse than
 * none. Thirty seconds is well under the interval the worker itself polls on, and each
 * of these is a handful of counts against a local SQLite file.
 */
const OPS_REFRESH_MS = 30_000;

export function useOpsOverview(days: number): UseQueryResult<OpsOverview> {
  return useQuery({
    queryKey: keys.opsOverview(days),
    refetchInterval: OPS_REFRESH_MS,
    queryFn: async () =>
      unwrap(await api.GET("/ops/overview", { params: { query: { days } } })),
  });
}

export function useOpsActivity(days: number): UseQueryResult<ActivityPoint[]> {
  return useQuery({
    queryKey: keys.opsActivity(days),
    placeholderData: (previous) => previous,
    queryFn: async () =>
      unwrap(await api.GET("/ops/activity", { params: { query: { days } } })),
  });
}

export function useOpsFailures(): UseQueryResult<FailureGroup[]> {
  return useQuery({
    queryKey: keys.opsFailures,
    refetchInterval: OPS_REFRESH_MS,
    queryFn: async () => unwrap(await api.GET("/ops/failures")),
  });
}

export function useOpsJobs(
  filters: { status: JobStatus | ""; kind: string; q: string },
  beforeId: number | null = null,
): UseQueryResult<Job[]> {
  const { status, kind, q } = filters;
  return useQuery({
    queryKey: [...keys.opsJobs(status, kind, q), beforeId],
    refetchInterval: OPS_REFRESH_MS,
    placeholderData: (previous) => previous,
    queryFn: async () =>
      unwrap(
        await api.GET("/ops/jobs", {
          params: {
            query: {
              limit: 50,
              ...(status ? { status } : {}),
              ...(kind ? { kind } : {}),
              ...(q ? { q } : {}),
              ...(beforeId === null ? {} : { before_id: beforeId }),
            },
          },
        }),
      ),
  });
}

/**
 * Every server's configuration, whoever is signed in.
 *
 * `useMyGuilds` is the same question asked of `/guilds`, and it answers with the caller's
 * own servers — the front door of an administrator's UI. This one is the operator's
 * inventory and belongs to nobody's servers, so the two are separate hooks over separate
 * routes rather than one hook with a flag.
 *
 * Settings do not move on their own, so neither of these polls: what changes here is
 * changed by a person in some server, and a stale row is worth less than a refetch every
 * thirty seconds of a hundred rows nobody is watching.
 */
export function useGuildConfigs(q: string): UseQueryResult<GuildConfigSummary[]> {
  return useQuery({
    queryKey: keys.opsGuilds(q),
    placeholderData: (previous) => previous,
    queryFn: async () =>
      unwrap(await api.GET("/ops/guilds", { params: { query: { ...(q ? { q } : {}) } } })),
  });
}

export function useGuildConfig(guildId: string): UseQueryResult<GuildConfig> {
  return useQuery({
    queryKey: keys.opsGuild(guildId),
    queryFn: async () =>
      unwrap(
        await api.GET("/ops/guilds/{guild_id}", {
          params: { path: { guild_id: guildId } },
        }),
      ),
  });
}
