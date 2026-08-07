# The backend is the only Discord client, and the only writer

Every action — from a slash command or from the web UI — is a call to the backend API.
The backend holds the bot token, makes all Discord REST calls, owns the SQLite file, runs
the enforcement job workers, and runs the audit scheduler in a single process.

The bot container holds only a gateway connection. It relays `INTERACTION_CREATE`,
`GUILD_MEMBER_ADD` and `GUILD_BAN_REMOVE` into the API and renders the API's response back
to Discord. It issues no Discord writes and touches no database. Slash commands arrive over
that same gateway rather than an HTTPS interactions webhook, which removes the ed25519
verification path and any need for public ingress on the bot — only the web UI is exposed.

Callers assert identity only, never authority: they send an actor's Discord user ID, and
the backend resolves that user's guild permissions against Discord itself (short-TTL
cached) before allowing anything. This is what makes ADR 0001's single enforcement point
real rather than nominal.

## Consequences

- Three containers: `backend`, `bot`, `web`. No broker, no second datastore.
- Exactly one process writes SQLite, which is the arrangement SQLite handles best. The
  cost is that slow enforcement sweeps share an event loop with API requests; if that
  becomes a problem, the worker loop can be split into a second container running the same
  image against a WAL-mode database.
- Within that process there are now two worker loops over the one queue, split by job
  kind: guild sweeps on one, everything else on the other. This is not the second
  container above and does not change who writes SQLite — it is one event loop, and both
  workers commit per (guild, user) pair rather than across a fan-out. It exists because a
  single queue position is the wrong unit of fairness between work that takes hours and
  work somebody is waiting on. See `timothy_api.enforcement.worker`.
- Slash commands stop working if the gateway connection drops, where the old HTTPS webhook
  would have survived it.
- Every authorised action costs a cached Discord permission lookup, inside Discord's
  three-second interaction deadline.
