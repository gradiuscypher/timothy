# Phase 5 → Phase 6 handoff

Paste this into a fresh session to pick the rewrite up at phase 6.

---

We're rewriting `banpool-tim-gcp` as **Timothy**, a Python service on Docker Compose with
SQLite. Read `CONTEXT.md` for the domain language, `PLAN.md` for the plan, and
`docs/adr/` for the nine decisions behind it. Those three are the source of truth —
this handoff only records what they don't. `docs/handoffs/phase-0.md` through
`phase-4.md` cover the scaffolding, the domain core, the API and enforcement, and the
bot, and are still accurate except where noted below.

**Phase 5 is complete and committed.** Start phase 6.

## What phase 5 built

`migration/`, a fourth workspace member (`timothy-migration`). 139 tests of its own, 637
in the workspace, all four checks clean (`ruff format`, `ruff check`, `ty check`,
`pytest`), 100% line and branch coverage on every migration module.

One command, `timothy-migrate`, with four subcommands in the order they are run:

- **`guilds`** — `GET /users/@me/guilds` with the bot token, written to a JSON snapshot.
  The only step that touches the network.
- **`import`** — a `mongodump` directory plus that snapshot in, a SQLite database out.
  Entirely offline.
- **`verify`** — the imported database against the dump it came from.
- **`diff`** — a dry run's audit log against the old bot's behaviour.

The runbook is `docs/cutover.md`. It is written to be executed top to bottom by somebody
who has not read the code, and it is the actual deliverable — the script is only how the
two checks get something to check.

## The two decisions the design turned on

**The source is a `mongodump` directory, not a live Mongo connection.** `pymongo` is a
dependency for `bson` alone, and `test_migration_boundaries.py` asserts nothing in the
package imports it. The point is repeatability: the same dump and snapshot produce the
same database, byte for byte and ID for ID, so a rehearsal is evidence about the real run
rather than a separate event with its own timing.

**The guild list comes from Discord, not from the dump.** Mongo never had a guild table —
a guild appeared in `subscriptions`, `exceptions` or `notifications` only once somebody
configured something there. The guilds ADR 0002 most exposes are exactly the ones that
configured *nothing* and rode the hardcoded `global` short-circuit, and deriving the list
from the dump would drop them. Fetching it into a file keeps the import offline anyway,
and makes the guild list a reviewable artefact instead of an API response nobody saw.

## What `verify` and `diff` are, and why both

`verify` is **static and complete**: every guild in the imported database crossed with
every user listed on either side, asked of both systems, using the real
`timothy_core.enforcement.decisions.decide` on Timothy's side and a transcription of
`is_user_banned_on_guild` on the old bot's. It never asks who is currently in a guild, so
it covers pairs nobody has seen in years.

`diff` is **dynamic and partial**: it reads `enforcement.dry_run` audit rows from a
rehearsal and classifies each. It adds live membership, live permissions and the workers
actually running. It cannot see silence — a skip writes nothing at all (ADR 0009) — so
under-enforcement is invisible to it. That gap is exactly what `verify` closes.
`test_the_diff_cannot_see_silence` states the limit as a test rather than as a comment.

Both sort findings into four verdicts. `newly enforced` must be zero and the CLI exits
`2` if it is not; `now warns instead of banning` and `no longer enforced` are intended
policy changes with ADRs behind them, and an operator agrees to the counts by hand.

## Things discovered in phase 5 that aren't in the docs

- **The old bot's live ban check never read `subscription_level`.**
  `is_user_banned_on_guild` — the only thing `bot.rs` called on a member join — asked
  `is_guild_subscribed` and banned if the answer was yes. Only the offline `tools.rs`
  sync, run by hand and rarely, ever looked at the level. So every guild holding a pool at
  `warn` has been getting bans from it, and `warn` working as CONTEXT.md defines it is a
  real behaviour change for those guilds. It is the `now warns` verdict, and it is why
  that verdict exists rather than being folded into "agrees".
- **The old `delete_pool` cascaded nothing.** It deleted one document. Every pool ever
  deleted left its listings and its subscriptions behind — and `is_guild_subscribed`
  matched the dead name, and `get_user_bans` never joined to `banpools`, so those
  listings were still being enforced. Expect orphans in quantity, and expect a
  `no longer enforced` count that is not zero.
- **`creator_id` of `"0"` becomes `Actor.system()`.** The old bot used it for its own
  actions and the previous SQLite → Mongo migration passed it as the author of every row
  it wrote. That is exactly what `actors.py` says `system` is for.
- **A subscription held at two levels keeps `ban`**, against the "first write wins" rule
  used for every other duplicate, because what the old bot *did* is the thing being
  preserved and the old bot banned. Reported loudly either way; it is a case for a human.
- **`enforcement_outcomes` is written empty and the import report says so at length.** An
  outcome is Timothy's claim to have issued a ban itself (ADR 0005). Every ban standing in
  these guilds is the old bot's, and inventing outcomes for them would arm the revert path
  against thousands of bans Timothy never placed — the first unsubscribe after cutover
  would lift them all.
- **Rows for guilds outside the snapshot are dropped.** The `guilds` table is the snapshot
  exactly. Importing a subscription for a guild that removed Timothy would give the sweep
  something to fail against every hour forever, which is phase 4's carried-forward noise
  arrived at from the other direction.
- **The import refuses a database that already holds data.** It assigns pool IDs from
  scratch, so it is not idempotent and there is no partial re-run. A migrated but empty
  database — the ordinary result of bringing the stack up once before cutover — is fine.
- **The migration's tests share `dumps.py`, not `conftest.py`**, for the same reason the
  bot's share `support.py`: `migration/tests` is a `ty` root rather than a package, so
  its module names share one namespace with `apps/bot/tests`. No test file is named the
  same in both, which is why the boundary test is `test_migration_boundaries.py`.

## What running the stack caught that the tests could not

Adding a fourth workspace member broke **both runtime images**. `uv sync
--no-install-workspace` still parses the root package's dependencies, so a member listed
in `[tool.uv.sources]` whose manifest is not in the build context fails the build
outright — and `.dockerignore` excluded `migration` wholesale. Both Dockerfiles now mount
and copy `migration/pyproject.toml`, and `.dockerignore` re-includes that one file. The
migration's *code* still never ships in an image.

CI gained the seam the unit tests cannot reach: a database written by `timothy-migrate`
outside any container, copied into the backend's volume, and served by the real backend
image — with the assertion that the `alembic_version` did not move, so the importer and
the backend are provably building the same schema. The whole sequence was rehearsed
locally in this session, including the build failure above.

One observation, not chased: with a placeholder token the backend now has a guild to
sweep, and discord.py logs `Unclosed client session` from `asyncio` after a failed login.
Harmless in CI and invisible in production, where the login succeeds. It is not covered by
the "shuts its workers down cleanly" step, which runs earlier.

## Carried forward

- **Nothing has actually been migrated.** Phase 5 built and rehearsed the tooling; the
  real dump, the real snapshot and the real cutover are an operator running
  `docs/cutover.md`. `verify` and `diff` have never been run against production data.
- **`diff` has no end-to-end exercise in CI.** Enforcement needs a real Discord token to
  produce a dry-run intention at all, so the CI stack step proves the import and the
  backend agree on the schema, and the unit tests prove the diff reads what the engine
  writes. The seam between them is covered only by `apps/api/tests/test_dry_run.py`
  pinning the audit row's shape.
- **A guild that removes Timothy while the bot is offline stays registered.** Unchanged
  from phase 4, and the import now has the same shape of answer: the guild snapshot is
  the authority at import time, and nothing prunes afterwards.
- **There is still no `GET /guilds`.** Phase 6 wants it. Phase 5 did not need it — the
  snapshot comes from Discord, not from Timothy.
- **GitHub Actions layer caching**, still deferred. Comment marks the spot in `ci.yml`.
- **The token is a single shared secret with no rotation story** (ADR 0008).
- **`is_member_of_any` still costs a scan** per non-member per TTL. Unchanged since
  phase 2.
- **`web/` is still a placeholder.** That is phase 6.

## Phase 6: web UI

Per PLAN.md: OAuth login and session, then parity screens, then the web-only work —
paginated search over listings, bulk operations, per-guild enforcement history, audit log.

Four things already in place that constrain it:

1. **The `sessions` table exists and is unused.** Phase 1 landed it with the schema;
   nothing issues a row yet.
2. **Same-origin, so no CORS at all.** nginx serves the SPA and proxies `/api` to the
   backend behind one Cloudflare Tunnel origin, and the session cookie works with
   `SameSite=Lax` without special cases (PLAN.md, "Shape").
3. **`?revert=true` is reachable only from the API and the web UI.** No slash command asks
   for it, deliberately — phase 4's handoff explains why. The UI is where "unsubscribe and
   unban everyone it touched" gets a deliberate, separate control.
4. **Pool renaming is web-only.** The surrogate key exists so it is possible; no slash
   command offers it.

The API client is generated from FastAPI's schema with `openapi-typescript` +
`openapi-fetch` (PLAN.md, Stack), so `GET /openapi.json` is the contract — the same one
`apps/bot/tests/test_contract.py` already reads for the bot.
