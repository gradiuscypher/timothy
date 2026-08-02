# Phase 4 → Phase 5 handoff

Paste this into a fresh session to pick the rewrite up at phase 5.

---

We're rewriting `banpool-tim-gcp` as **Timothy**, a Python service on Docker Compose with
SQLite. Read `CONTEXT.md` for the domain language, `PLAN.md` for the plan, and
`docs/adr/` for the nine decisions behind it. Those three are the source of truth —
this handoff only records what they don't. `docs/handoffs/phase-0.md` through
`phase-3.md` cover the scaffolding, the domain core, the API and enforcement, and are
still accurate except where noted below.

**Phase 4 is complete and committed.** Start phase 5.

## What phase 4 built

The bot, in `apps/bot/src/timothy_bot/`. 498 tests, all four checks clean
(`ruff format`, `ruff check`, `ty check`, `pytest`), 97% line and branch coverage, 100%
on every bot module.

- **`api.py`** — the backend as the bot sees it. One method per endpoint, every call
  carrying the bearer token and an explicit `X-Timothy-Actor`. Path segments are
  percent-encoded, so a pool called `spam/ham` addresses one route rather than two.
  Failures — including transport failures — arrive as one `ApiError` carrying the
  backend's own `detail`.
- **`commands/`** — the sixteen slash commands, one module per part of the domain, plus
  `base.py` for the three things every handler does (find the backend as *this*
  moderator, find the guild, answer). `__init__.install()` puts the global set on the
  tree and the management set in the management guild.
- **`embeds.py`** — the green/red embed shape the old bot answered in, preserved.
- **`relay.py`** — the four gateway events, on their way to the backend as `system`.
  Every one of them swallows `ApiError`: a backend that is down must not take the gateway
  connection with it.
- **`client.py`** — the gateway client. Intents, the command tree, `tree.sync()`, the
  five `on_*` handlers, and a last-resort error handler so a bug never leaves an
  interaction unanswered.
- **`__main__.py`** — reach the backend, then reach Discord, in that order.

## The command surface

`apps/bot/tests/command_surface.json` is `tools/slash_cli/json_commands/` from the old
repository, verbatim. `test_command_surface.py` renders what discord.py would upload and
compares every key the old definitions declared. Names, descriptions, option names,
option order, types, choices, `dm_permission` and `default_member_permissions` are all
pinned. `slash_cli` and `json_commands/` are retired: the surface is now declared where it
is implemented, so what exists and what is uploaded cannot drift.

Four deliberate departures from the old bot, all of them visible in the tests that assert
them:

- **`add_notification` only offers text channels.** The old option accepted any channel
  and the bot answered "Provided channel was not a text channel" afterwards; Discord's
  picker rules it out up front now, and the after-the-fact branch is gone. This is the
  only change to a payload.
- **`list_subscriptions` no longer prints a fabricated `global:ban` line.** ADR 0002 in
  one line: `global` is an ordinary pool, and a guild that has unsubscribed has to be able
  to see that it has.
- **`add_ban`'s "User" field is a mention.** The old bot spent a REST call to render
  `name#discriminator`, and every discriminator now reads `#0`. The bot also makes no
  Discord calls of its own.
- **`list_pools` says why it failed.** The old bot dropped the reason on this one command
  and kept it on its siblings. It is the only command a member with no administrator
  anywhere can reach, so "not permitted" is the answer they most need.

Everything else a moderator sees is the old wording, including
`delete_subscription`'s "this does not remove the bans already in place" — which is still
true, because no slash command asks the API for a `revert`. The `?revert=true` flag exists
on four routes and is reachable only from the API and, in phase 6, the web UI. Giving
"tidy up a pool" and "unban everyone it touched" the same keystroke is not something to
do by accident.

`delete_subscription`'s `pool_name` option description is the literal string `pool_name` —
a typo in the original, preserved on purpose. It is one line in
`commands/subscriptions.py` if it should go.

## Things decided or discovered in phase 4 that aren't in the docs

- **The bot never asks Discord for anything.** Not even to render a name. ADR 0003 says
  the backend is the only Discord client; the gateway connection is the one exception, and
  it is read-only. That is why `add_ban` mentions rather than fetches.
- **`snowflake()` parses the `user_id` options, and rejects `0`.** The options are strings
  and always have been: Discord's `USER` option type only offers people the client can
  resolve, and half the point of a shared pool is listing someone who is not in your
  guild. The old bot `unwrap`ped the parse and panicked.
- **Nothing defers.** The API answers inside Discord's three-second deadline for
  everything a command does, because a mutation enqueues its fan-out rather than
  performing it. `TIMOTHY_REQUEST_TIMEOUT` is 2.5s for the same reason: an answer that
  arrives after the deadline cannot be delivered, so waiting for it only costs the
  moderator their error message.
- **`on_ready` registers guilds but never deregisters them.** The gateway's guild list is
  not evidence of a departure — a partial `READY` during a Discord outage looks exactly
  like being kicked from everything at once, and deregistering cascades a guild's
  subscriptions, exceptions and notification channel away. `on_guild_remove` is the only
  signal that is evidence, and Discord raises a different event for unavailability. See
  "Carried forward" for what this leaves open.
- **`TIMOTHY_GATEWAY_ENABLED=false` is what CI runs.** The bot would otherwise try to log
  in with CI's placeholder token and crash-loop, and `--wait` would fail. Everything up to
  the login is still exercised. It is also the flag for a local session run against a
  production application.
- **`TIMOTHY_SYNC_COMMANDS`** exists because uploading now happens on startup: a second
  instance run against the same Discord application would otherwise overwrite the live
  command surface just by starting.
- **The bot's tests share `support.py`, not `conftest.py`.** Both test directories would
  be packages called `tests`, and the type checker resolves only the first of them.
  `support.py` is a plain module on `pythonpath`; `conftest.py` holds fixtures alone.
- **httpx's per-request INFO logging is turned down.** The relay already logs a line per
  event saying what the backend decided, and two lines per gateway event is one too many.

## What CI now proves about the bot

Beyond the unit tests, the `stack` job runs three things against the built image:

- the bot reaches the backend *before* it tries to reach Discord;
- the bot's own `Api` client is accepted by the real backend — the internal token, the
  actor header and the routes together, which the unit tests cannot prove because the bot
  cannot import the backend — and a `user:` actor is refused a `SYSTEM` route;
- the command tree builds inside the image: ten global, six in the management guild.

`test_contract.py` covers the other half of that seam, comparing every path literal in
`api.py` against the API's own `GET /openapi.json`. A route renamed in `apps/api` fails
there rather than in production.

The whole stack was built, run and torn down locally in this session, and every one of
those steps was rehearsed against it.

## Carried forward

- **A guild that removes Timothy while the bot is offline stays registered.** Its
  subscriptions keep being swept, and the bans fail with a Discord 403, which
  `enforcement_outcomes` records as `failed` and retries — harmless, but noisy forever.
  The fix is an operator running `DELETE /guilds/{id}`, and it is deliberately not
  automatic: see the `on_ready` note above. If it becomes a real nuisance, the safe
  version is a `GET /guilds` listing plus a *manual* reconcile command, never a
  prune driven by gateway state.
- **There is still no `GET /guilds`.** Phase 6 wants it. Phase 4 did not add it, because
  the only use it had here was the prune above.
- **GitHub Actions layer caching**, still deferred. Comment marks the spot in `ci.yml`.
- **The token is a single shared secret with no rotation story** (ADR 0008).
- **`is_member_of_any` still costs a scan** per non-member per TTL. Unchanged since
  phase 2.
- **No `migration/` directory yet.** That is phase 5. **`web/` is still a placeholder.**
  Phase 6.

## Phase 5: migration and cutover

Per PLAN.md: a one-shot Mongo → SQLite import in `migration/`.

Four things already in place that constrain it:

1. **Pools use a surrogate key.** The import has to build a name → id map first and
   rewrite every listing and subscription foreign key as it goes (PLAN.md, Schema).
2. **`global` must be materialised.** ADR 0002 dropped the reserved name, so the import
   has to write a real `global` subscription row for every guild the bot is currently in,
   or those guilds silently stop enforcing the shared banlist. `guilds` rows have to exist
   for them too, since everything cascades off that table.
3. **`adminroles` and `serverconfig` are dead** and are not imported. Their commands are
   in the old repo's `json_commands/archive/`.
4. **The rehearsal is the deliverable, not the script.** PLAN.md asks for a dry run
   against production data with `TIMOTHY_DRY_RUN=true`, diffing Timothy's intended
   actions against the old bot's behaviour before dry run goes off. `enforcement_outcomes`
   with `dry_run` intentions recorded (ADR 0009) is what that diff reads.

The old Mongo schema is in `rust/db_wrapper/src/mongo.rs` and
`rust/axum_interactions/src/db/mongo.rs` in `/home/gradius/banpool-tim-gcp`; the migration
binary that repo already had is `rust/axum_interactions/src/bin/migration.rs`, and it is
worth reading for the field names even though nothing of it is reusable.
