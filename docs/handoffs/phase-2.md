# Phase 2 → Phase 3 handoff

Paste this into a fresh session to pick the rewrite up at phase 3.

---

We're rewriting `banpool-tim-gcp` as **Timothy**, a Python service on Docker Compose with
SQLite. Read `CONTEXT.md` for the domain language, `PLAN.md` for the plan, and
`docs/adr/` for the seven decisions behind it. Those three are the source of truth —
this handoff only records what they don't. `docs/handoffs/phase-0.md` and
`docs/handoffs/phase-1.md` cover the scaffolding and the domain core and are still
accurate.

**Phase 2 is complete and committed.** Start phase 3.

## What phase 2 built

The API, in `apps/api`. 259 tests, all four checks clean
(`ruff format`, `ruff check`, `ty check`, `pytest`). `timothy_api` is at 96% line and
branch coverage, and every module but `__main__.py` is at 100%.

- **Database wiring.** `timothy_api.db.Database` holds the one engine the process gets,
  and `create_app`'s lifespan runs `upgrade_to_head` before serving. The revisions ship
  inside the wheel, so a container on an empty volume brings its own schema.
- **`timothy_api.settings`** now carries every setting in PLAN.md's Configuration table.
  `DRY_RUN`, `ENFORCEMENT_BURST_LIMIT` and `SWEEP_INTERVAL` are declared but unread —
  they are yours.
- **`timothy_api.identity`** — a bearer token authenticates the *caller*, and
  `X-Timothy-Actor` (`user:<snowflake>` or `system`) names the *actor*. See the decision
  note below; this is the one thing phase 2 added that no ADR called for.
- **`timothy_api.policy`** — ADR 0001's single policy module. An `Operation` maps to one
  `Requirement`, and `allows()` decides. `timothy_api.deps.Requires` resolves only the
  fact the requirement names and turns a refusal into a 403.
- **`timothy_api.permissions`** — `PermissionResolver` over `DiscordPort`, with a TTL
  cache at `PERMISSION_CACHE_TTL`.
- **`timothy_api.discord_adapter`** — the production `DiscordPort`, over discord.py,
  REST-only and logging in lazily. `translate()` is the only place discord.py's
  exceptions exist.
- **The CRUD surface** — pools, listings, guilds, subscriptions, exceptions, notification
  channel, audit log. 13 paths; `GET /openapi.json` is the contract phase 6 generates its
  client from.
- **`timothy_api.audit`** — a row per mutation, in the mutation's own transaction.
- **`timothy_api.jobs`** — `enqueue()` and seven `JobKind`s, also in the mutation's
  transaction. **Nothing drains them. That is phase 3's first job.**

## The decision phase 1's handoff asked me to make

**Mutations enqueue.** The alternative — write the row and enqueue nothing until phase 3
— would have meant retrofitting every endpoint later, and would have lost the property
that makes it correct: a job written in the same transaction as the change that justifies
it cannot be committed without it, or run before it.

The payloads are deliberately thin. They name **what changed**, never what to do about
it. Which guilds a new listing reaches is a question about subscriptions at the moment
the worker runs, not the moment a moderator typed the command, so the fan-out is yours
and is not baked into the queue. The seven kinds and what each one's payload means are
documented in `jobs.py`; change them freely if the worker wants a different shape.

## Things decided or discovered in phase 2 that aren't in the docs

- **The API needs a shared secret, and this is new.** nginx proxies `/api` from what will
  be a public Cloudflare tunnel. ADR 0003 says callers assert identity and the backend
  resolves authority — but with no caller authentication, "I am acting for user 123" is
  an authority assertion, and anyone reaching the tunnel could claim to be an
  administrator. So every route but `/health` and `/openapi.json` requires
  `TIMOTHY_INTERNAL_TOKEN` as a bearer credential. An unset token refuses everything
  rather than accepting everything. **This is a security decision no ADR covers, and it
  probably deserves one.** Phase 6's session cookie is what will authenticate browsers;
  until then a browser reaching `/api` gets a 401, which is correct.
- **A system actor is refused everything except its own operations.** `Requirement.SYSTEM`
  covers guild registration and deregistration only — the things that follow the bot
  joining or leaving, where there is no human and so no Discord permission to derive
  authority from. Letting `system` stand in for a human would be an unaudited bypass of
  ADR 0001 rather than an application of it. The consequence for you: **the auto-exception
  after a manual unban (ADR 0006) must not go through `PUT /guilds/{id}/exceptions/{user}`**
  — that route requires a human administrator. Handle the `GUILD_BAN_REMOVE` event
  in-process, decide with `should_except_after_unban`, and write the row directly.
- **Snowflakes are strings on the wire.** They are 64-bit and today's are around 1.4e18,
  well past 2^53 where a JavaScript number stops being exact. Discord's own API does this.
  `timothy_api.schemas.Snowflake` handles it; they stay `int` everywhere inside the
  process. Any new response model must use it.
- **Coverage was lying, by a lot.** SQLAlchemy's async layer switches greenlets on every
  `await`, and `TestClient` runs the app in its own thread. Without
  `concurrency = ["thread", "greenlet"]` in `[tool.coverage.run]`, request handlers report
  as unexecuted — the routers read as ~55% covered while their tests passed. Fixing it
  turned up three genuinely dead functions. If a number looks impossible, it is.
- **FastAPI evaluates dependency annotations at import.** Modules with dependencies or
  routes therefore do *not* use `from __future__ import annotations`, and their types are
  imported at runtime. `deps.py`'s module docstring says so; the failure mode is a
  `PydanticUserError` about a type that "is not fully defined" when the OpenAPI schema is
  generated, which does not name the real cause.
- **FastAPI's own auth errors disagree with each other** — a missing bearer is a 403 and a
  missing API key a 401. Both schemes are `auto_error=False` and `identity.py` raises its
  own: 401 for no/wrong token, 400 for a garbled actor.
- **Handlers commit for themselves.** A `yield` dependency is closed only *after* the
  response is sent, so a commit in the session dependency could fail with the client
  already holding a 200.
- **`ruff`'s `EM101`/`EM102`/`TRY003` are off for `apps/api/src`.** `HTTPException(detail=…)`
  is the message the caller reads — the API's contract, not exception hygiene.
- **`apps/api/tests/` is a package** (`__init__.py`) so its modules can share `conftest`'s
  constants, and `apps/api` is a `ty` root so the relative import resolves. The other test
  directories need neither.
- **`/data` is created in the API image, owned by `timothy`.** Docker seeds a fresh named
  volume from the image's mount point including its ownership; created at first mount it
  would be root's, and the non-root backend could not write the database it is the sole
  writer of. **If you have a `timothy_data` volume left from phase 0 or 1, it is
  root-owned — `docker compose down -v` before the first phase-2 run.**
- **Lowering a subscription from `ban` to `warn` enqueues nothing.** Asking to stop banning
  from now on is not asking to undo what was already done; that is what `revert` on the
  unsubscribe is for.
- **Creating an exception enqueues nothing either, and that is an open question**, not a
  decision. Whether an exception should lift a ban Timothy has *already* issued is a
  revert, and reverts are ADR 0005's territory. Settle it in phase 3 rather than by
  implication. `exceptions.py` says so at the point it would go.

## Carried forward

- **GitHub Actions layer caching**, still deferred. Comment marks the spot in `ci.yml`.
- **The compose stack was not run in this session** — the sandbox had no access to the
  Docker socket. `docker compose config` validates and CI's `stack` job exercises it, now
  including two new assertions: that `/api` refuses a caller without the token, and that
  the backend migrated `/data/timothy.db` on startup. **Watch that job on the first push.**
- **`jobs` still has no `last_error` column.** Add one with a migration when the worker
  wants retry visibility.
- **Reading pools costs a scan.** `is_member_of_any` walks Timothy's guilds with
  `fetch_member` until one hits, and caches the aggregate answer per user for the TTL, so
  a non-member costs one call per guild once a minute. Fine at this size. Phase 6 can
  shortcut it for browser callers, whose OAuth `guilds` scope already names their guilds.
- **There is no `GET /guilds`** listing every guild Timothy is in, and no route over
  `enforcement_outcomes`. Phase 6 wants both (per-guild enforcement history); phase 3 may
  want the latter sooner for its own verification.
- **The warn copy still drops PLAN.md's `>` prefixes.** Check it against a real channel
  before phase 3 ships it.
- **No `migration/` directory yet.** Phase 5. **`web/` is still a placeholder.** Phase 6.

## Phase 3: enforcement

Per PLAN.md: the job worker loop, rate-limited fan-out, retries with backoff, enforcement
outcomes recorded. Dry run, circuit breaker and per-guild pause. The sweep scheduler as a
safety net, staggered across guilds. Revert paths, including suppressing Timothy's own
`GUILD_BAN_REMOVE` events so a revert never creates an exception.

Read ADRs 0004, 0005 and 0007 first. Four things already in place that constrain it:

1. **`timothy_core.enforcement.decisions` already decides.** `decide()` takes an
   `EnforcementRequest` and returns `Ban | Warn | Skip`. The worker's job is to gather the
   state, call it, and carry out the answer — not to re-derive it. Its module docstring
   holds the precedence argument and the reasoning about exceptions suppressing warnings.
2. **`enforcement_outcomes` is the durable record**, with no foreign keys, so it survives
   the pool or listing that caused it. `Ban.justifications` is a tuple because a ban
   records an outcome for *every* justifying pool — `decide_revert` can only ask about
   pools it recorded.
3. **`DiscordPort` is the only door**, and `FakeDiscord` can rate limit, lose members, and
   fail one call in a fan-out while the rest land. Test the breaker against it.
4. **`Settings.dry_run`, `enforcement_burst_limit` and `sweep_interval` are already
   parsed.** `dry_run` fails safe — anything unreadable means on.

The worker runs in the same process as the API (ADR 0003), so it shares the event loop
and the engine. `app.state.db.sessions` is the factory; take a session per unit of work
rather than holding one open across a fan-out.
