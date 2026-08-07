# User names are backfilled from Discord, on a job that reads

Every screen in the web UI is a list of snowflakes. A listing, an exception, an
enforcement outcome, an audit entry: each one names a person by an eighteen-digit number,
and the moderator reading it has to paste that number into Discord to find out who it is.
Timothy already knows several of these names in passing and throws them away.

Keeping them is cheap, and the first version of this did only that: record what a login
and a relayed gateway event happen to carry, and show the ID for anybody else. It has one
flaw, and it is fatal to the point of the feature. **The free sources only ever name
people who turn up.** A user is on a pool precisely because guilds want them gone, so
they will not be joining one; the migrated data arrived as tens of thousands of bare IDs,
none of which will ever be named by traffic. The one screen where recognition matters
most would have stayed a wall of numbers indefinitely.

So Timothy asks Discord. `GET /users/{id}` is a cheap unauthenticated-by-guild lookup that
needs no intent, no membership and no permission — the bot token can resolve any ID — and
a daily job walks the IDs that appear on a page and have never been looked up.

## What this costs, and what keeps it small

This widens ADR 0007's deliberately narrow port from six operations to seven, and the
seventh is the first that exists for the UI rather than for enforcement. That is the real
decision here, and four constraints are what make it acceptable:

- **It reads.** `fetch_user` cannot ban, cannot unban and cannot post. The rails in
  ADR 0007 exist because the port can act on people; nothing this operation returns is
  allowed to reach a decision, and `user_names` is a label table no enforcement code
  imports.
- **It goes through the worker.** The backfill is a `JobKind`, not a loop of its own, so
  its lookups are serialised behind the same single worker that issues bans (ADR 0003).
  A backfill can therefore never race enforcement for Discord's rate limit; it waits.
- **It is capped and it is idempotent.** A round looks up at most
  `USERNAME_BACKFILL_BATCH` IDs, and every ID is looked up once ever — including the ones
  Discord has no user for, which are recorded as a NULL name rather than skipped. Without
  that last part a deleted account would be re-fetched every day for the life of the
  deployment.
- **It yields rather than argues.** A rate limit or an outage that outlasts the usual
  backoff ends the round, keeping the names already learned. Nothing waits on a name, so
  the correct response to Discord saying "not now" is to stop until tomorrow.

## Consequences

- The port has an operation that names no guild, and the fake grew a `users` dictionary
  that is not inside any `FakeGuild`. Both are new shapes in code that was uniformly
  guild-scoped.
- The backlog clears over days rather than at once: the migrated data is 3,076 listings,
  so at five hundred a day it is named within a week, and the steady state after that is
  a handful of new listings a day. The cap exists to keep one round short and
  interruptible rather than because the backlog is large — the arithmetic that matters is
  PLAN.md's two lookups a second, not the ~347,000 figure beside it, which is a sweep
  round (listings × subscribing guilds) and not a count of people.
- `user_names.name` is nullable, and NULL means "asked, and Discord had nobody" rather
  than "unknown". A reader never sees the difference: both draw the ID.
- Names are a snapshot and go stale. A user who renames themselves is shown their old
  name until something names them again, and nothing re-fetches on a schedule. Recognition
  is what this is for, and a stale name serves it; if that stops being true the fix is to
  age rows out, not to poll.
