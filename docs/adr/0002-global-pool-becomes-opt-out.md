# The global pool becomes opt-out

The original bot hardcoded `global` as a reserved pool name: `is_guild_subscribed`
short-circuited to `true` for it, no subscription row ever existed, and the subscription
listing prepended a fake `global:ban` line. A guild could not leave the shared banlist.

We are dropping the special case. `global` becomes an ordinary pool. Guilds are
auto-subscribed to it when the bot joins, preserving today's effective behaviour, but a
guild administrator may unsubscribe. This is a deliberate policy change, not just a
refactor — a server can now decline the shared banlist while still using its own pools.

## Consequences

- No reserved pool names and no `mandatory` flag; the ban-check path has no special cases.
- Subscription listings reflect actual stored state.
- Migration must materialise a real `global` subscription row for every guild the bot is
  currently in, or those guilds silently stop enforcing the shared banlist.
