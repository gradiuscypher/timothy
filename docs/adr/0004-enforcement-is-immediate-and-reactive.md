# Enforcement is immediate, but reactive

Creating a listing used to insert a row and nothing else; the user was banned only when
they next joined a guild or when the hourly audit swept them up, a lag of up to an hour.

Creating a listing now enqueues one enforcement job per subscribing guild. Workers drain
the queue against Discord's rate limits and record the outcome per guild, so failures are
visible and retryable rather than silent.

Enforcement stays **reactive**: a ban is issued only for a user who is currently in the
guild or who joins it later. We rejected pre-emptive banning — banning every listed user
in every subscribing guild regardless of membership. It offers stronger protection, but at
this scale the initial backfill is millions of rate-limited ban calls, and it would add
tens of thousands of entries to every subscribing guild's ban list and audit log.

## Consequences

- The audit sweep is demoted from the primary enforcement mechanism to a safety net for
  events missed during gateway downtime, and can run far less aggressively.
- A user listed while absent is banned at the door on join, not before.
- Per-guild enforcement outcomes become queryable, which is what the old backlog wanted
  under "retroactive ban failure correction".
