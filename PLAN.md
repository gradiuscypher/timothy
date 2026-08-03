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
| Frontend | Vite, React 19, TypeScript, TanStack Router + Query, Tailwind v4 |
| API client | `openapi-typescript` + `openapi-fetch`, generated from FastAPI's schema |
| Tests | pytest + anyio, real SQLite per test, in-memory fake Discord; Vitest + msw |
| Ingress | Cloudflare Tunnel (`cloudflared`), no published ports |

shadcn/ui was in this table and is not installed. Phase 6 wrote the same primitives in its
idiom — the same Tailwind vocabulary, the same `cn` helper — because what shadcn brings to
screens like these is Radix behind the interactive components, and the interactions here
are a `<select>`, a table and one confirm dialog. See `web/src/components/ui.tsx`; the
classes are compatible, so `shadcn add` later drops in over the top rather than beside it.

## Configuration

| Setting | Default | Notes |
| --- | --- | --- |
| `MANAGEMENT_GUILD_ID` | — | Exactly one. The guild pool authority is held in, and the guild you have to be a member of to sign in to the web UI at all (ADR 0013). Unset closes login too. |
| `POOL_MANAGER_ROLE_IDS` | — | The roles there whose holders own pools and listings. Administering that guild is not enough, and unset closes pool management for everybody (ADR 0012). |
| `OWNER_IDS` | — | Whoever runs the deployment. Gates `/ops` and nothing else. Unset closes it for everybody (ADR 0011). |
| `DRY_RUN` | `true` | Fails safe — unparseable means on. |
| `ENFORCEMENT_BURST_LIMIT` | `25` | Bans in one guild in one run before the breaker trips. Runtime-adjustable, not a redeploy. |
| `SWEEP_INTERVAL` | `7d` | Must be longer than a round takes, and a round is one member lookup per listed user per subscribed guild, issued serially. This was `1h` on the reasoning that "an hour of exposure is tolerable and a day is not" — right about the tolerance, wrong about the arithmetic. See below. |
| `PERMISSION_CACHE_TTL` | `60s` | |
| `DISCORD_CLIENT_ID` / `_SECRET` | — | The web UI's OAuth login. Unset closes login (503) rather than opening anything. |
| `PUBLIC_BASE_URL` | — | Where a browser reaches Timothy. The redirect URI is `<this>/api/auth/callback` and Discord matches it exactly. |
| `SESSION_LIFETIME` | `7d` | Also how long a session's guild snapshot stays current (ADR 0010). |
| `SESSION_COOKIE_SECURE` | `true` | Off only for a local stack on plain HTTP. |
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
sessions              id PK (sha256 of the cookie), user_id, username, avatar,
                        guild_ids, created_at, expires_at
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
short-TTL cached. There are two kinds of caller and they assert it differently: a service
presents the internal token and names an actor in `X-Timothy-Actor`; a browser presents
the session cookie, which *is* the actor and so cannot name a different one (ADR 0008,
ADR 0010).

| Operation | Requires |
| --- | --- |
| pools, listings | a role named in `POOL_MANAGER_ROLE_IDS`, held in the management guild |
| subscriptions, exceptions, notification channel | `ADMINISTRATOR` in the target guild |
| reading pools and listings | membership of any guild the bot is in |
| the audit log | a role named in `POOL_MANAGER_ROLE_IDS` |
| the operations view | being named in `OWNER_IDS` |

The first and last rules are configured as well as derived, and for related reasons. "Who
runs this deployment" is not a fact Discord has at all. "Which role owns the pools" is a
fact Discord holds but cannot be guessed — so the deployment names the role, and Discord
still answers who holds it, live, on every request.

Both only ever narrow. `ADMINISTRATOR` in the management guild is a permission for running
a Discord server; deciding who gets banned from every subscribing guild is not the same
job, and it should not arrive as a side effect of the first (ADR 0012). Neither setting
falls back when unset: pool management and the operations view close rather than reverting
to whoever used to have them. ADR 0011 draws the line between this and the in-app RBAC
ADR 0001 rejected — what is stored is a configuration value, not a record of grants.

## Warn notifications

A `warn` subscription never bans. The first time a listed user is seen in the guild —
whether by joining or by a sweep — Timothy posts once to the notification channel and
records a `warned` outcome, which prevents it ever warning about that user and pool again.

The copy has to make the counterfactual obvious: nothing happened, but something would
have. It arrives as an embed titled "Heads up — no action taken", coloured yellow — a ban
notice is red, and the colour is the part a moderator reads before the words.

> **Heads up — no action taken**
> <@{user}> is listed in **{pool}**, which you're subscribed to at **warn** level.
> They're still in your server.
> **Listed for:** {reason}
> Had **{pool}** been set to *ban*, they would have been removed. Switch with
> `/add_subscription {pool} ban`. You won't be warned about this user again.

## What a sweep costs

Measured against the migrated data, because the original hourly default was chosen without
this arithmetic and does not survive it.

A round asks Discord "is this user in this guild?" once per listed user per pool their
guild subscribes to, for every guild — **~347,000 lookups** for 123 guilds and 3,076
listings. The worker holds one job at a time and awaits each call, so they go out
serially at about **two a second**: roughly **48 hours** per round.

That cost does not fall away after the first round. A candidate stops being one when its
outcome settles, and only `banned`, `warned` and `skipped_exception` settle. A listed user
who is merely *absent* records nothing, deliberately — settling them would have the sweep
skip them forever if the gateway later missed their join, which is the exact gap the sweep
exists to cover. Almost every candidate is absent, so the set barely shrinks: one guild
went 2,995 → 2,992 after a completed round.

So the sweep is a **weekly** net here, and `SWEEP_INTERVAL` is set to say so. An interval
shorter than a round does not sweep more often — a guild with one outstanding is skipped —
it only leaves the backend calling Discord continuously and forever.

None of this touches the primary path. A listed user who joins is banned at the door by
the gateway event, immediately, and that path never consults the candidate set (ADR 0004).

**The two a second is serial issuance, not Discord's pacing.** Per-guild buckets cap around
five calls a second, separate guilds have separate buckets, and 30 concurrent lookups
across 30 guilds measured **43/s** — which would put a round at a couple of hours and make
an hourly-ish net achievable again. Sweeping guilds concurrently is therefore the cheap
fix, ahead of the bulk member listing that would make it cheaper still. Both are deferred
until the system has run for a while: concurrency here means concurrent writes to a SQLite
database with a single writer, and that deserves its own careful pass rather than a rushed
one before a cutover.

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
paginated search over listings, bulk operations, per-guild enforcement history, audit log,
and an operations view for whoever is running the thing.

## Carried-over backlog this resolves

From the old README: immediate banning on listing creation (phase 3), retroactive ban
failure correction (enforcement outcomes), action audit logs (phase 2), OAuth'd ban lookup
page (phase 6), unban-on-unsubscribe (ADR 0005), warn notifications (ADR 0002's sibling
decision), pagination (phase 6), bulk bans (phase 6), guild/server naming (CONTEXT.md),
centralising `ban_diff` (phase 1).
