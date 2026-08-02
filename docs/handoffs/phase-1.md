# Phase 1 → Phase 2 handoff

Paste this into a fresh session to pick the rewrite up at phase 2.

---

We're rewriting `banpool-tim-gcp` as **Timothy**, a Python service on Docker Compose with
SQLite. Read `CONTEXT.md` for the domain language, `PLAN.md` for the plan, and
`docs/adr/` for the seven decisions behind it. Those three are the source of truth —
this handoff only records what they don't. `docs/handoffs/phase-0.md` covers the
scaffolding and is still accurate.

**Phase 1 is complete and committed** at `bc36c79` ("phase 1 complete"). Start phase 2.

## What phase 1 built

All of it in `packages/core`, none of it touching the network.

- **`timothy_core.db.models`** — the eleven tables from PLAN.md's Schema section.
  `timothy_core.db.columns` holds two `TypeDecorator`s: `UtcDateTime`, which rejects
  naive datetimes and re-tags UTC on the way out, and `ActorColumn`.
  `timothy_core.db.engine.make_engine` attaches the pragmas — foreign keys are **off**
  in SQLite by default and every cascade is inert without them.
- **`timothy_core.actors.Actor`** — a Discord user or Timothy itself, stored as
  `user:<snowflake>` or `system`. This is ADR 0006's "first-class system actor",
  replacing the old bot's magic user ID `"0"`.
- **`timothy_core.migrations`** — Alembic, packaged inside the wheel so the container
  migrates without a checkout. `upgrade_to_head(url)` / `downgrade_to_base(url)` are
  synchronous; call them via `asyncio.to_thread` if a loop is already running.
  `packages/core/alembic.ini` is for the CLI during development and nothing reads it at
  runtime.
- **`timothy_core.ports.discord`** — the `DiscordPort` protocol (ban, unban,
  fetch_member, guild_permissions, post_message) plus `DiscordError` and its four
  subclasses. **`timothy_core.ports.fake.FakeDiscord`** implements it in memory and can
  be told to rate limit, to lose members, and to fail one call in a fan-out while the
  rest land.
- **`timothy_core.enforcement.decisions`** — `decide()`, `decide_revert()` and
  `should_except_after_unban()`. Pure functions over plain values; no database, no
  Discord, no clock. `timothy_core.enforcement.messages` holds the warn copy and the
  ban audit reason.

80 tests, all four checks clean (`ruff format`, `ruff check`, `ty check`, `pytest`).
The domain layer is at 100% line and branch coverage; keep it there.

## Things decided or discovered in phase 1 that aren't in the docs

- **An exception suppresses warnings, not just bans.** CONTEXT.md defines an Exception in
  terms of bans, and this is the one place phase 1 had to go past the docs. The warn copy
  tells a moderator a ban *would* have happened, which is exactly what the exception says
  will never happen; posting it anyway contradicts a decision the guild already made.
  The argument is written into `decisions.py`'s module docstring. **If you disagree, this
  is the sort of thing that deserves an ADR rather than a quiet edit.**
- **Skip precedence is a decision, not an accident.** Paused → not listed → absent →
  exception. Only the exception skip is meant to be recorded as an outcome; the other
  three deliberately record nothing so that resuming, subscribing or joining still
  enforces. The order and the reasoning are in the same docstring, and
  `test_skip_precedence` pins it.
- **A ban records an outcome for *every* justifying pool, not the first.** ADR 0005's
  revert asks whether another live listing still holds the ban up, and it can only ask
  that of pools it recorded. `Ban.justifications` is therefore a tuple.
- **Ban beats warn.** If any pool listing a user is held at `ban`, they are banned and no
  warning is posted at all.
- **pysqlite only opens a transaction for DML.** The first cut of `migrations/env.py`
  created every table and stamped nothing: the `CREATE TABLE`s ran in autocommit while
  the `alembic_version` insert sat in an uncommitted transaction. `env.py` now calls
  `connection.commit()` explicitly and `test_upgrade_records_the_revision` guards it.
  Expect the same shape of bug anywhere else a bare `connect()` writes rows.
- **`enforcement_outcomes` has no foreign keys at all.** Deliberate: it is durable state,
  and cascading it away with a deleted pool would silently destroy the ability to revert.
  Its composite primary key `(guild_id, user_id, pool_id)` *is* the warn-dedupe key, and
  rows are updated in place. Everything else guild-scoped does cascade from `guilds`, so
  the bot leaving a guild cleans up its configuration.
- **Enum columns carry a real CHECK constraint.** `create_constraint=True` is not the
  default; without it `level` would accept any string at all.
- **A leaked SQLAlchemy engine fails an unrelated test.** A pooled SQLite connection
  collected by the GC raises during finalisation, and `filterwarnings = ["error"]` turns
  that into an error in whichever test runs next. The `sync_engine` fixture disposes
  explicitly; do the same for any engine you make by hand.
- **Port exceptions are named `…Error`** (`NotFoundError`, `ForbiddenError`,
  `RateLimitedError`, `DiscordUnavailableError`) because ruff's `N818` insists, not
  because Discord calls them that.
- **Snowflakes are plain `int`.** `NewType` was considered and dropped: the friction at
  every boundary outweighed the safety. Domain functions take keyword arguments instead,
  so `guild_id` and `user_id` are hard to transpose.

## Carried forward

- **GitHub Actions layer caching**, still deferred. Comment marks the spot in `ci.yml`.
- **Nothing runs the migrations yet.** The backend has no database wiring at all — no
  engine, no session dependency, no startup migration. That is the first thing phase 2
  needs.
- **`jobs`, `sessions` and `audit_log` are tables with no code behind them.** Phases 3,
  6 and 2 respectively. `jobs` has no `last_error` column; add one with a migration when
  phase 3 wants retry visibility.
- **The warn copy drops PLAN.md's `>` prefixes**, read as document quoting rather than a
  Discord blockquote. Check that against a real channel before phase 3 ships it.
- **No `migration/` directory yet** (the Mongo import). Phase 5.
- **`web/` is still a placeholder.** Phase 6.
- **Domain settings are still unimplemented** — `MANAGEMENT_GUILD_ID`, `DRY_RUN`,
  `ENFORCEMENT_BURST_LIMIT`, `SWEEP_INTERVAL`, `PERMISSION_CACHE_TTL`. `DRY_RUN` fails
  safe: unparseable means on (ADR 0007).

## Phase 2: the API

Per PLAN.md: FastAPI over the domain, permission resolution and caching, the full CRUD
surface, OpenAPI published for client generation, and an audit-log row on every mutation.

Read ADR 0001 and ADR 0003 first. Two things they constrain directly:

1. **Callers assert identity, never authority.** The bot and the web UI send an actor's
   Discord user ID; the backend resolves that user's permissions against Discord itself,
   cached at `PERMISSION_CACHE_TTL`. `DiscordPort.guild_permissions` is already there for
   it, and a non-member resolves to `GuildPermissions.none()` so the deny path has one
   shape.
2. **Permission decisions live in one policy module**, not inlined at each endpoint —
   ADR 0001 anticipates relaxing "who may look up a listing" to a subscribing guild's own
   moderators, and wants that to be a change to one rule.

The authorization table is in PLAN.md. Enforcement itself is phase 3: phase 2 should
write the listing and enqueue nothing yet, or enqueue into `jobs` and leave the worker
for later — decide which and say so in the handoff.
