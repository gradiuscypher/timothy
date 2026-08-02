# Reverting bans requires recorded attribution

Removing a listing, deleting a pool or unsubscribing a guild previously left every
resulting ban in place — `delete_subscription` said so in its own success message. The old
backlog wanted an "option to remove the bans" but it was never safe to build, because
nothing recorded which bans Timothy had caused and which the guild had made itself.

Because enforcement now records an outcome per (listing, guild), reverting is possible.
Removal takes an optional `revert` flag, defaulting to off. When set, Timothy unbans only
users it has a recorded enforcement outcome for, and only where no other still-active
listing independently justifies the ban. A guild's own bans are never touched.

## Consequences

- Enforcement outcomes are durable state, not just logs. They cannot be pruned without
  losing the ability to revert.
- Timothy's own unbans raise `GUILD_BAN_REMOVE` on the gateway, which would otherwise
  trigger the auto-exception behaviour from ADR 0006 and permanently exempt the very users
  it just readmitted. The revert path must mark these unbans as self-inflicted and the
  event handler must ignore them.
