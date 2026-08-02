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

`migration/`, a fourth workspace member (`timothy-migration`). 139 tests of its own, 642
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
actually running. It cannot see silence — a skip writes no *audit* row — so
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
  listings were still being enforced. That predicted orphans in quantity — and the real
  dump had **none**: all five pools are live and every listing and subscription points at
  one. The `no longer enforced` bucket came back empty. The handling is still right, and
  the prediction was wrong.
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

## What the rehearsal found

The tooling was built, then run against the production dump and a real Discord token in
dry run. `verify` covered all 123 guilds: 378,348 pairs, **zero unexplained findings**,
and the only differences were 2,935 `now warns` — all in one guild. A fractional dry run
against that guild then exercised the workers end to end and the diff came back clean.

Four things came out of it that reading the code had not:

**`fetch_member` runs at about two per second per guild.** Measured, not estimated: 40
lookups in 15.7s in a burst, and 2,995 in 28 minutes sustained. A full round of the
production data is ~347,000 lookups — **about 48 hours**.

And it is *every* round, not a cold start. Only `banned`, `warned` and `skipped_exception`
settle a candidate; a listed user who is merely absent records nothing, deliberately —
settling them would have the sweep skip them forever if the gateway later missed their
join, which is the exact gap the sweep exists to cover. Almost every candidate is absent,
so the set barely shrinks: the rehearsal's guild went 2,995 → 2,992 after a completed
round, verified by running `sweep_candidates` against the result.

So `TIMOTHY_SWEEP_INTERVAL` schedules rounds but does not set the period — the outstanding-
job guard means the real period is however long a round takes. **The safety net is a
two-day net at this scale, not an hourly one**, and the backend makes Discord calls
continuously and forever, almost all of them answering "not here".

The primary path is untouched: a join is enforced immediately by the gateway event, which
never consults the candidate set. So this is a weakened backstop rather than an outage —
but it makes the change below considerably more than an optimisation.

**The warn burst is standing exposure, not a burst.** 2,935 users would be warned about in
that guild *if present*; one actually was. The rest stay armed and trickle out as people
join. Worth telling that guild what changes, not worth bracing for.

**The breaker counted bans only.** So a warn-level subscription had no ceiling at all, and
that guild was sitting behind an uncapped path to 2,935 notifications. Fixed: the limit now
counts enforcement actions, bans and notifications sharing one per-guild budget. ADR 0007's
consequences record it.

**`/list_pools` did not work at 123 guilds.** Reading pools requires membership of *any*
guild Timothy is in, which `is_member_of_any` answers by asking Discord once per guild
until one says yes. Measured across the real deployment: **51.9 seconds** for a full scan.
The bot gives up after 2.5s and Discord closes the interaction at 3s, so any user whose
guild was late in the scan order got a timeout — and `/list_pools` is the one command a
member with no administrator anywhere can reach, so it was the users with the least power
who hit it.

Fixed by having the bot name the guild the interaction came from
(`X-Timothy-From-Guild`) and the backend check that guild first. It is a hint and grants
nothing: the answer still comes from Discord, the full scan still runs behind it, and a
header naming a guild the caller is not in costs one wasted call and changes no decision
(ADR 0001 intact). Measured after: **1.31 seconds**, one call. Two tests pin it — that it
reorders, and that an outsider claiming a guild is still refused.

This was phase 2 code that was correct and became wrong at scale. Nothing but the guild
count made it visible.

**The sweep's skip-guard only looked at pending jobs.** A guild whose sweep is still
*running* — which, at half an hour per guild, is most of them — picked up a second job each
round. Fixed to include `RUNNING`. Both fixes have tests that fail without them.

## A correction to ADR 0009

The ADR said dry run "writes no outcomes at all". It does write `skipped_exception`, and
the rehearsal surfaced three real ones. The ADR was wrong, not the code: that row is not an
attribution, no revert can act on it (reverting keys strictly on `banned`), and it stops
the sweep re-asking Discord about excepted users every round — which at two lookups a
second is not free. ADR 0009 now carves it out explicitly and `test_an_exception_is_still_recorded`
pins it, because it looks like a leak until you know why it is not.

`docs/cutover.md`'s tripwire was corrected with it. A non-zero `enforcement_outcomes` in
dry run is not on its own evidence that dry run is off; the *statuses* are what to check.

## The one open decision: bulk member listing

Upgraded, on the evidence above, from an optimisation to the thing that makes
`TIMOTHY_SWEEP_INTERVAL` mean what it says.

The sweep asks Discord "is this user in this guild?" once per candidate. Discord also
offers `GET /guilds/{id}/members?limit=1000`, which pages the whole membership — one
request per thousand members, regardless of how many users are listed.

| approach | requests for the full first round |
| --- | --- |
| per-user (today) | 347,407 → ~48 hours |
| bulk, guilds of 10k members | 1,230 → ~10 minutes |
| bulk, guilds of 200k members | 24,600 → ~3.5 hours |

Bulk wins whenever a guild has fewer than 2.8 million members, which is every guild that
exists. It was left undone deliberately, because it is not a cutover setting:

- it needs a sixth `DiscordPort` operation, and ADR 0007 keeps that surface at five on
  purpose — "narrow enough to read in one sitting, and narrow enough for a fake to
  implement honestly";
- it depends on the privileged `GUILD_MEMBERS` intent, which `.env.example` already
  requires for `GUILD_MEMBER_ADD` but which would become load-bearing for enforcement too;
- it changes what a sweep *is* — a diff against a membership snapshot rather than a series
  of questions — and that deserves its own ADR.

Worth doing before the deployment grows. Not worth doing between now and cutover.

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
- **The token is a single shared secret with no rotation story** (ADR 0008), and the
  production one was pasted into a session transcript during the rehearsal. **Rotate it.**
- **Bulk member listing**, above — the standing answer to a sweep whose real period is
  two days rather than the configured hour.
- **`is_member_of_any` still costs a full scan for a genuine non-member** — the hint
  above only helps someone who *is* in the guild they are calling from, which is everyone
  arriving through the bot. A caller in none of Timothy's guilds still pays 123 calls
  before being refused, which is 52 seconds of Discord's budget spent saying no. Phase 6's
  OAuth `guilds` scope names the caller's guilds up front and closes this properly for
  browser callers; the bot has no equivalent.
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
