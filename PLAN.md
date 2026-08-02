# Timothy rewrite plan

Rewrite of `banpool-tim-gcp` (Rust on GCP, MongoDB) as a Python service on Docker Compose
with SQLite. Domain language is in [CONTEXT.md](./CONTEXT.md); the decisions behind this
plan are in [docs/adr/](./docs/adr/).

## Shape

Three containers. The backend is the only thing that talks to Discord, the only thing that
writes the database, and the only place authorization is enforced. The bot and the web UI
are frontends over its API.

```
                 ┌──────────────────────────────┐
   Discord ──────│ bot        gateway only      │
   gateway       │            relays events     │
                 └──────────────┬───────────────┘
                                │ HTTP (internal)
                 ┌──────────────▼───────────────┐
                 │ backend                      │
                 │   FastAPI                    │──── Discord REST
                 │   enforcement workers        │     (bot token)
                 │   sweep scheduler            │
                 │   SQLite (sole writer)       │
                 └──────────────▲───────────────┘
                                │ /api  (internal)
                 ┌──────────────┴───────────────┐
                 │ web        nginx             │
                 │   serves the SPA             │
                 │   proxies /api → backend     │
                 └──────────────▲───────────────┘
                                │
                 ┌──────────────┴───────────────┐
   browser ──────│ cloudflared   tunnel, TLS    │
                 └──────────────────────────────┘
```

Nothing binds a public port. Cloudflare Tunnel is the only ingress, and it fronts a single
origin: nginx serves the SPA and reverse-proxies `/api` to the backend. Same-origin means
no CORS configuration at all, and the session cookie works with `SameSite=Lax` without
special cases.

## Stack

| Concern | Choice |
| --- | --- |
| Runtime | Python 3.13, `uv` workspace |
| Typing / lint | `ty`, `ruff` (format + lint), strict settings |
| API | FastAPI, Pydantic v2 |
| Persistence | SQLAlchemy 2.0 async + aiosqlite, Alembic migrations |
| Discord | discord.py — gateway in the bot, REST-only (`login()` without connect) in the backend |
| Frontend | Vite, React 19, TypeScript, TanStack Router + Query, Tailwind + shadcn/ui |
| API client | `openapi-typescript` + `openapi-fetch`, generated from FastAPI's schema |
| Tests | pytest + anyio, real SQLite per test, in-memory fake Discord; Vitest + Playwright |
| Ingress | Cloudflare Tunnel (`cloudflared`), no published ports |

## Configuration

| Setting | Default | Notes |
| --- | --- | --- |
| `MANAGEMENT_GUILD_ID` | — | Exactly one. Administrators here own pools and listings. |
| `DRY_RUN` | `true` | Fails safe — unparseable means on. |
| `ENFORCEMENT_BURST_LIMIT` | `25` | Bans in one guild in one run before the breaker trips. Runtime-adjustable, not a redeploy. |
| `SWEEP_INTERVAL` | `1h` | Staggered across guilds. Hourly over daily: if a sweep catches a miss, an hour of exposure is tolerable and a day is not. |
| `PERMISSION_CACHE_TTL` | `60s` | |
| `WORKERS_ENABLED` | `true` | The worker and sweep scheduler run inside the backend. Off leaves the API serving and the queue accumulating. |
| `JOB_POLL_INTERVAL` | `1s` | How long the worker waits on an empty queue — the floor on how immediate "immediate" is. |
| `JOB_MAX_ATTEMPTS` | `5` | Attempts before a job is abandoned as `failed`, with the reason in `jobs.last_error`. Per-guild failures are recorded as enforcement outcomes and retried by the sweep instead. |
| OAuth scopes | `identify guilds` | `guilds` so the UI can list the user's guilds directly; authority still resolved server-side. |

## Layout

```
timothy/
├── pyproject.toml          uv workspace root
├── packages/core/          domain, schema, Discord port, enforcement engine
├── apps/api/               FastAPI, authz, workers, scheduler
├── apps/bot/               gateway relay — discord.py + httpx only, no core
├── web/                    React SPA + nginx config
├── migration/              one-shot Mongo → SQLite import
└── compose.yaml            backend, bot, web, cloudflared
```

The bot deliberately does not depend on `core`. It relays events and renders responses;
it has no domain logic to share.

## Schema

```
pools                 id PK autoinc, name UNIQUE, description, created_by, created_at
listings              id PK autoinc, user_id, pool_id FK, reason, created_by, created_at
                        UNIQUE (user_id, pool_id)
subscriptions         guild_id, pool_id FK, level ('ban'|'warn'), created_by, created_at
                        PK (guild_id, pool_id)
exceptions            guild_id, user_id, created_by, created_at
                        PK (guild_id, user_id)
notification_channels guild_id PK, channel_id, created_by, created_at
guilds                guild_id PK, joined_at, enforcement_paused
enforcement_outcomes  guild_id, user_id, pool_id, status, reason, attempted_at
                        status: banned | warned | failed | skipped_exception
                        UNIQUE (guild_id, user_id, pool_id)
                        — durable; makes a ban attributable and revertable,
                          and is the record that keeps warnings to one per user
jobs                  id, kind, payload, run_after, attempts, status, last_error
sessions              id PK, user_id, created_at, expires_at
audit_log             id, actor, action, target, detail, at
```

Pools use a surrogate key rather than the name, so a pool can be renamed without rewriting
every listing and subscription. The name stays unique and remains what humans type — slash
commands and API paths resolve by name — but nothing references it. The Mongo import builds
a name → id map first and rewrites foreign keys as it goes.

Dropped from Mongo as dead: `adminroles`, `serverconfig`. Neither has live callers — their
slash commands are in `json_commands/archive/`.

`enforcement_outcomes` doing double duty is deliberate. A `warned` row is both the audit
trail and the dedupe key: a user warned in a guild is never warned again there for the same
pool, even across leaves and rejoins. If that guild later switches the subscription from
`warn` to `ban`, the next sweep picks up the members who are still present.

## Authorization

Callers assert identity only. The backend resolves permissions itself against Discord,
short-TTL cached.

| Operation | Requires |
| --- | --- |
| pools, listings | `ADMINISTRATOR` in the management guild |
| subscriptions, exceptions, notification channel | `ADMINISTRATOR` in the target guild |
| reading pools and listings | membership of any guild the bot is in |

## Warn notifications

A `warn` subscription never bans. The first time a listed user is seen in the guild —
whether by joining or by a sweep — Timothy posts once to the notification channel and
records a `warned` outcome, which prevents it ever warning about that user and pool again.

The copy has to make the counterfactual obvious: nothing happened, but something would
have.

> **Heads up — no action taken**
> <@{user}> is listed in **{pool}**, which you're subscribed to at **warn** level.
> They're still in your server.
> **Listed for:** {reason}
> Had **{pool}** been set to *ban*, they would have been removed. Switch with
> `/add_subscription {pool} ban`. You won't be warned about this user again.

## Phases

**0 — Scaffolding.** uv workspace, ruff and ty configured strict, pytest, Dockerfiles,
`compose.yaml`, CI. Nothing domain-specific.

**1 — Domain core.** Schema and Alembic migrations. The `DiscordPort` protocol and its
in-memory fake. Pure enforcement decision logic: given a user, a guild and the current
listings, subscriptions and exceptions, what should happen? Fully tested with no network.
This phase is where correctness is won.

**2 — API.** FastAPI over the domain, permission resolution and caching, the full CRUD
surface, OpenAPI schema published for client generation. Audit log written on every
mutation.

**3 — Enforcement.** Job table and worker loop, rate-limited fan-out, retries with backoff,
enforcement outcomes recorded. Dry run, circuit breaker and per-guild pause. Sweep
scheduler as a safety net, staggered across guilds. Revert paths, including suppression of
Timothy's own `GUILD_BAN_REMOVE` events so a revert never creates an exception.

**4 — Bot.** discord.py gateway client. Slash commands re-implemented against the API with
their existing names, options and flat structure preserved exactly — `/add_ban` creates a
Listing, and that vocabulary split is deliberate (see CONTEXT.md). Global and
management-guild command sets stay split as they are today, with `default_member_permissions`
and `dm_permission` unchanged; they are now a second line of defence rather than the only
one. Event relay for member join and unban. Command registration moves into the bot,
retiring the Python `slash_cli` tool and its `json_commands/` tree.

Pool renaming is web-only — no slash command for it.

**5 — Migration and cutover.** Mongo → SQLite import, including materialising a real
`global` subscription row per live guild (ADR 0002). Rehearse against a dump; verify counts
and spot-check; run the new stack in dry run against production data and diff its intended
actions against the old bot's behaviour before switching dry run off.

**6 — Web UI.** OAuth login and session, then parity screens, then the web-only work:
paginated search over listings, bulk operations, per-guild enforcement history, audit log.

## Carried-over backlog this resolves

From the old README: immediate banning on listing creation (phase 3), retroactive ban
failure correction (enforcement outcomes), action audit logs (phase 2), OAuth'd ban lookup
page (phase 6), unban-on-unsubscribe (ADR 0005), warn notifications (ADR 0002's sibling
decision), pagination (phase 6), bulk bans (phase 6), guild/server naming (CONTEXT.md),
centralising `ban_diff` (phase 1).
