# Phase 3 → Phase 4 handoff

Paste this into a fresh session to pick the rewrite up at phase 4.

---

We're rewriting `banpool-tim-gcp` as **Timothy**, a Python service on Docker Compose with
SQLite. Read `CONTEXT.md` for the domain language, `PLAN.md` for the plan, and
`docs/adr/` for the nine decisions behind it. Those three are the source of truth —
this handoff only records what they don't. `docs/handoffs/phase-0.md` through
`phase-2.md` cover the scaffolding, the domain core and the API, and are still accurate
except where noted below.

**Phase 3 is complete and committed.** Start phase 4.

## What phase 3 built

Enforcement, in `apps/api/src/timothy_api/enforcement/`. 360 tests, all four checks clean
(`ruff format`, `ruff check`, `ty check`, `pytest`), 96% line and branch coverage.

- **`worker.py`** — claims one job at a time, dispatches, retries with exponential backoff,
  and abandons after `JOB_MAX_ATTEMPTS` with the reason in the new `jobs.last_error`
  column (migration `0002`). `recover()` returns jobs left `running` by a crash.
- **`handlers.py`** — one function per `JobKind`, turning a thin payload into the set of
  (guild, user) questions it implies *now*. A test asserts every kind has a handler.
- **`state.py`** — gathers the `EnforcementRequest`, and works out the fan-out targets.
- **`engine.py`** — the `Enforcer`: dry run, the circuit breaker, the ban or the
  notification, and the outcome recorded afterwards.
- **`revert.py`** — the four revert paths, which only ever touch bans with a recorded
  `banned` outcome.
- **`sweep.py`** — queues one `ENFORCE_GUILD` per guild per `SWEEP_INTERVAL`, staggered
  across the interval using the queue's own `run_after`, skipping guilds that already
  have one pending.
- **`pacing.py`** — how the two loops are shut down. See "Things discovered" below.
- **`selfunbans.py`** — the in-process registry that stops a revert's own
  `GUILD_BAN_REMOVE` becoming an exception.
- **`routers/events.py`** — `POST /events/member-join` and `POST /events/ban-remove`,
  both `Requirement.SYSTEM`. **This is the surface phase 4's bot relays to.**
- **`routers/enforcement.py`** — `GET /guilds/{id}/enforcement`, optionally filtered by
  status. Per-guild enforcement history, which phase 6 wants and phase 3 needed to see
  what a fan-out actually did.

## The decisions phase 2's handoff asked me to make

**Creating an exception now takes `?revert=true`, defaulting off.** Phase 2 left this
open. The answer keeps every revert in the codebase the same shape — opt-in, never
touching a ban without a recorded outcome. Creating an exception silently would make it
the one action that unbans people as a side effect, and the flow a moderator actually has
for "let this person back in" is the unban itself, which ADR 0006's hook already turns
into an exception. The one thing `revert_for_exception` does *not* ask is
`still_justified`: an exception is precisely the guild deciding its subscriptions do not
reach this person, so running it through `decide_revert` would answer STILL_JUSTIFIED
every time and make the flag do nothing.

**The internal token got its ADR** (0008), as phase 2 suggested it should.

**Dry run got one too** (0009), because where it records turned out to be a real decision
rather than a detail — see below.

**The warn copy keeps dropping PLAN.md's `>` prefixes.** They are the documents' own
convention for setting a block off (CONTEXT.md uses `>` the same way for an editorial
aside), not part of the message. Left as phase 1 had it. If it looks wrong in a real
channel, it is one line in `timothy_core.enforcement.messages`.

## Things decided or discovered in phase 3 that aren't in the docs

- **Discord first, then the record.** A ban is issued and only then recorded. Crashing in
  between loses the attribution, so a later revert refuses to lift a ban it really did
  cause — conservative and fixable by hand. The other order loses in the dangerous
  direction.
- **What counts as a job failure is narrow.** A guild that refuses a ban, a user who
  outranks Timothy, a channel that was deleted — none of those fail the job. They become
  `failed` enforcement outcomes, and the sweep retries them when the world may have
  changed. Rate limits and outages are retried around the individual call
  (`retry.py`) so a fan-out does not burn a job's attempts on pacing. What reaches the
  job-level retry is the job failing to *run*.
- **`failed` is deliberately not a settled status.** `state.SETTLED` is the reason a sweep
  in a steady-state guild costs almost nothing: it only looks at users with no settled
  outcome. Leaving `failed` out of that set is ADR 0004's "retroactive ban failure
  correction", in one line.
- **The circuit breaker's threshold is per guild per *run*.** Listing twenty-five people
  one at a time does not trip it, and should not: the rail is looking for a burst, which
  arrives as a single fan-out. `test_the_threshold_is_per_run` pins that down so it does
  not get "fixed" later.
- **A revert ignores the per-guild pause.** The pause stops Timothy acting *against* a
  guild's members; a revert only readmits them. Honouring it would disable the remedy at
  the exact moment it is needed, since the usual reason a guild is paused is that the
  breaker just tripped on a bad bulk listing.
- **Background loops are asked to stop, never cancelled.** A task cancelled part-way
  through a transaction cannot finish closing its session — the cleanup is itself a
  coroutine, and the next `await` raises `CancelledError` again — so the connection is
  only released when the GC gets to it, after the engine has been disposed. This showed up
  as a `ResourceWarning` failing an *unrelated* test. `pacing.Pacer` is the fix, and it is
  also the test seam: a loop paced by something that says "stop" after two rounds runs
  exactly two rounds.
- **`TIMOTHY_WORKERS_ENABLED=false` in the test fixtures.** Most of the suite asserts what
  the API records, including what it enqueues, and a background worker would make every
  such assertion a race. `settings_overrides` is the fixture a module overrides to change
  configuration; the base fixture also turns **dry run off**, because a suite that
  inherited the fail-safe default would be a suite in which Timothy never bans anybody.
- **A `banned` user is no longer in the guild**, in the fake as in Discord. So a second
  pool listing an already-banned user records nothing — which is why `still_justified`
  asks the *live listings* rather than the recorded outcomes. `test_reverts.py` says so at
  the point it matters.

## Two bugs the compose stack had, found by running it

Phase 2 could not run the stack. Both of these had passed every unit test.

- **Every documented duration crashed the backend at startup.** `.env.example` and
  `compose.yaml` have always said durations are "seconds, or ISO 8601", and compose
  defaults `TIMOTHY_SWEEP_INTERVAL=3600`. Pydantic's `timedelta` parsing rejects a bare
  number in a string, so the documented configuration meant the container never started.
  `settings.Duration` accepts both now, and `test_the_whole_compose_environment_starts_the_process`
  parses every value compose supplies together — the check that was missing.
- **Nothing Timothy logged ever reached the container's output.** Uvicorn configures its
  own loggers and leaves the root logger alone, so the worker starting, a guild the
  breaker paused, and a job abandoned after its last attempt were all written to a handler
  that did not exist. `__main__.py` now calls `logging.basicConfig`.

**CI's `stack` job now asserts both**, plus that the queue drains end to end (registering
a guild is the only mutation that needs no Discord call) and that the backend shuts its
workers down with exit code 0 and no leaked connections. The whole stack was built, run
and torn down locally in this session; every one of those steps was rehearsed against it.

## Carried forward

- **GitHub Actions layer caching**, still deferred. Comment marks the spot in `ci.yml`.
- **The token is a single shared secret with no rotation story** (ADR 0008). Proportionate
  while the only holders are containers in one compose network.
- **There is still no `GET /guilds`** listing every guild Timothy is in. Phase 6 wants it;
  nothing needed it yet.
- **`is_member_of_any` still costs a scan** per non-member per TTL. Unchanged from phase 2.
- **No `migration/` directory yet.** Phase 5. **`web/` is still a placeholder.** Phase 6.

## Phase 4: the bot

Per PLAN.md: a discord.py gateway client. The slash commands re-implemented against the
API with their existing names, options and flat structure preserved exactly — `/add_ban`
creates a Listing, and that vocabulary split is deliberate (CONTEXT.md). Global and
management-guild command sets stay split as they are today, with
`default_member_permissions` and `dm_permission` unchanged. Command registration moves
into the bot, retiring `slash_cli` and `json_commands/`.

Four things already in place that constrain it:

1. **The bot does not depend on `core`** (PLAN.md's Layout). It relays events and renders
   responses; it has no domain logic to share. `apps/bot` currently has discord.py and
   httpx and nothing else — keep it that way.
2. **`GET /openapi.json` is the contract.** Phase 6 generates a TypeScript client from it;
   the bot can read it too rather than hand-rolling paths.
3. **Two headers on every call.** `Authorization: Bearer $TIMOTHY_INTERNAL_TOKEN`, and
   `X-Timothy-Actor: user:<snowflake>` naming the moderator who typed the command. Never
   `system` for a human's command — `Requirement.SYSTEM` is refused everything a person
   owns, and vice versa.
4. **The event relay already has somewhere to go.** `POST /events/member-join` and
   `POST /events/ban-remove`, both with `X-Timothy-Actor: system`, both answering 202 with
   a one-line `action` describing what the backend decided. Log that line — it is how an
   operator sees whether an auto-exception fired or was suppressed as Timothy's own.

The API answers within Discord's three-second interaction deadline for everything except
a fan-out, which it does not do inline: mutations enqueue and return. So a slash command
can reply immediately and truthfully — "listed; enforcement is under way" — rather than
deferring.
