# Timothy

A shared moderation service for Discord. Guilds pool the users they've banned, subscribe
to each other's pools, and Timothy enforces those pools as bans in every subscribing
guild. A Discord bot and a web UI are both thin clients over one backend API.

## Language

### Pools and listings

**Pool**:
A named, curated list of Discord users that guilds can subscribe to.
_Avoid_: banpool, banlist, list

**Listing**:
A record that a user belongs on a pool, carrying the reason and who added it. A listing
is an assertion, not an action — it does not itself ban anyone.
_Avoid_: ban (in this sense), entry, pool member

> The slash commands deliberately keep their original names — `/add_ban`, `/delete_ban`,
> `/get_user_bans` — because moderators have years of muscle memory in them. So `/add_ban`
> creates a Listing. This split between the Discord surface and the domain language is
> intentional; do not "fix" it.

**Ban**:
The Discord action of banning a user from a guild. Timothy issues one when a listing is
enforced in a guild that subscribes at ban level.
_Avoid_: guild ban, enforcement

### Guilds and subscriptions

**Guild**:
A Discord server. Always `guild`, never `server`, matching Discord's own API.
_Avoid_: server

**Management Guild**:
The single guild whose administrators may create pools and manage listings. Authority
over pools is derived from holding Administrator here.
_Avoid_: AM server, admin server, home guild

**Subscription**:
A guild's decision to enforce a pool, held at a level of either `ban` or `warn`.
_Avoid_: enrollment, opt-in

**Subscription Level**:
Either `ban` (listings are enforced as Discord bans) or `warn` (listings are reported to
the guild's notification channel and never banned).

**Exception**:
A guild's declaration that a specific user is never to be banned by Timothy in that
guild, regardless of which pools list them. Guild-wide, never scoped to one pool.
_Avoid_: allowlist, whitelist, vouch, override

### Enforcement

**Sweep**:
The periodic pass over a guild that enforces any listing that should already have been
enforced there. A safety net for events missed while the gateway was down, not the primary
enforcement path.
_Avoid_: audit, sync, reconciliation, scan

**Audit Log**:
The append-only record of every action taken through Timothy and who took it, covering
both human actions and Timothy's own. Unrelated to a Sweep.
_Avoid_: action log, activity log, history

**Notification Channel**:
The channel in a guild where Timothy reports what it did there — warn-level matches, bans
issued, exceptions created.

**Enforcement**:
The act of applying a listing to a guild that subscribes to its pool. At `ban` level that
means issuing a ban; at `warn` level, a message to the notification channel.

**Enforcement Outcome**:
The recorded result of enforcing one listing in one guild — succeeded, failed, or skipped
because of an exception. This is what makes a ban attributable to Timothy, and so what
makes reverting a ban safe.

**Revert**:
Undoing a ban Timothy issued, after the listing or subscription that justified it goes
away. Only ever applies to bans with a recorded enforcement outcome, never to a guild's
own bans.
_Avoid_: unban (that is the raw Discord action), rollback, undo

**Dry Run**:
The mode in which Timothy records every enforcement it would perform but issues nothing to
Discord. Fails safe: if the setting cannot be read, dry run is on.
_Avoid_: benchmark, test mode, simulation
