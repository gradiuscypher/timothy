# Discord sits behind a port, with safety rails in front of it

Timothy's consequential action is banning real people, and it is not meaningfully
reversible at the far end. Two decisions follow.

Everything Timothy needs from Discord — ban, unban, fetch member, resolve a member's guild
permissions, post a message — is expressed as a narrow protocol. A discord.py adapter
implements it in production; an in-memory fake implements it in tests, recording bans and
simulating rate limits, missing members and partial failures. The enforcement engine, the
authorization checks and the sweep logic are therefore testable at full speed with no
network and no mocking of third-party internals.

Three rails sit between the domain logic and the adapter:

- **Dry run** — records every enforcement it would perform and issues nothing. Defaults to
  on if the setting cannot be read, as the old `DISCORD_BOT_BENCHMARK` flag did.
- **Circuit breaker** — halts a run and marks the guild degraded when a single sweep or
  fan-out would exceed a burst threshold of bans for one guild, then requires a manual
  resume. This is the rail that catches a bad migration or an accidental bulk listing
  before it lands.
- **Per-guild pause** — isolates one misbehaving guild without stopping the service.

## Consequences

- The domain layer never imports discord.py.
- A legitimate large operation (a genuine bulk listing) will trip the breaker and need an
  explicit resume. That is the intended trade.
