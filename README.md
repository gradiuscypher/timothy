# timothy
a discord moderation tool

See [CONTEXT.md](./CONTEXT.md) for the domain language, [PLAN.md](./PLAN.md) for the
rewrite plan, and [docs/adr/](./docs/adr/) for the decisions behind it.

## Development

Requires [uv](https://docs.astral.sh/uv/). Python 3.13 is fetched automatically.

```sh
uv sync              # create the workspace venv
uv run ruff format . # format
uv run ruff check .  # lint
uv run ty check      # type check
uv run pytest        # test
```

The web UI is a separate toolchain in `web/`. Node 24.

```sh
cd web
npm install
npm run api          # regenerate the API client from the backend's OpenAPI document
npx eslint .         # lint
npx tsc -b --noEmit  # type check
npx vitest run       # test
npm run dev          # serve the SPA, proxying /api to http://localhost:8000
```

`src/api/schema.d.ts` is generated and **committed**, so the contract is a reviewable file
rather than a build-time fetch. CI regenerates it and fails if the committed copy has
drifted — run `npm run api` and commit the result whenever the API changes.

## Running

```sh
cp .env.example .env  # every variable there is commented; the tokens are required
docker compose up --build
```

No service publishes a port. The Cloudflare Tunnel is the only ingress and its single
origin is `http://web:80`, which serves the SPA and proxies `/api` to the backend.

### The web UI's login

`TIMOTHY_DISCORD_CLIENT_ID`, `TIMOTHY_DISCORD_CLIENT_SECRET` and
`TIMOTHY_PUBLIC_BASE_URL` turn on browser login. Leaving them unset closes it — the stack
comes up and `/api/auth/login` answers 503 naming what is missing.

On the Discord application, under OAuth2, register the redirect URI **exactly** as
`<TIMOTHY_PUBLIC_BASE_URL>/api/auth/callback`. Discord compares the string, and a trailing
slash is a failed login with no useful message. The backend requests `identify guilds`;
nothing else.

Timothy stores no Discord user tokens. The access token is used once, at login, to ask who
you are and which servers you are in, and then discarded — what you may *do* is resolved
with the bot token on every request (ADR 0001, ADR 0010).

## Migrating from the old bot

`migration/` holds the one-shot Mongo → SQLite import and the two checks that decide
whether the cutover happens. [docs/cutover.md](./docs/cutover.md) is the runbook; run it
top to bottom.

```sh
uv run timothy-migrate guilds --output guilds.json          # the one online step
uv run timothy-migrate import --dump ./dump --guilds guilds.json --database timothy.db
uv run timothy-migrate verify --dump ./dump --database timothy.db
uv run timothy-migrate diff   --dump ./dump --database timothy.db   # after a dry run
```

The importer reads a `mongodump` directory rather than a live database, so the same inputs
give the same output and a rehearsal is evidence about the real run.

## Slash commands

The bot registers them itself, on startup, from `apps/bot/src/timothy_bot/commands/` —
there is no separate upload step. Two sets: the guild-configuration commands are global,
and the pool and listing commands exist only in `TIMOTHY_MANAGEMENT_GUILD_ID`. Both are
administrator-only and unavailable in DMs, which the backend enforces again on its own
account after resolving the caller against Discord.

`apps/bot/tests/command_surface.json` is the surface as it shipped before the rewrite;
the tests compare against it, so a rename that would cost a moderator their muscle memory
fails the build.

Two settings matter when running a second instance against the same Discord application:
`TIMOTHY_SYNC_COMMANDS=false` stops it overwriting the live command surface, and
`TIMOTHY_GATEWAY_ENABLED=false` stops it connecting at all.

The application needs the **Server Members** privileged intent. Without it Discord never
sends `GUILD_MEMBER_ADD`, so a listed user who joins is not banned at the door — nothing
else looks wrong, and the weekly sweep still catches them.

## Calling the API

There are two kinds of caller, and neither of them asserts authority.

A **service** — the bot, the migration tool, you with `curl` — presents the internal token
as a bearer credential and names the Discord user it is speaking for in `X-Timothy-Actor`.
The header carries no authority of its own; what that user may do is resolved against
Discord.

```sh
curl -H "Authorization: Bearer $TIMOTHY_INTERNAL_TOKEN" \
     -H "X-Timothy-Actor: user:242024455190577152" \
     http://localhost/api/pools
```

A **browser** presents the session cookie `/api/auth/login` issues, which names the actor
itself — so there is nothing to assert and nothing to forge. Sending `X-Timothy-Actor`
alongside a session is refused rather than ignored.

Everything but `/health`, `/openapi.json` and `/auth/*` needs one of the two.

Guild, user and channel IDs are **strings** in requests and responses. They are 64-bit,
and JavaScript numbers are not.

## Watching it run

`/ops` in the web UI, for whoever is named in `TIMOTHY_OWNER_IDS` — set that to your own
Discord user ID. Administering the management server makes somebody responsible for the
pools, not for Timothy itself, so this is a separate and much smaller list (ADR 0011).
Unset closes the page for everybody; it never falls back.

It answers the questions that come up during a cutover and afterwards: is dry run still on, are the
workers running, how far through the sweep is it, which server is producing all the
failures, and what is stuck in the queue.

Counts over time come from the append-only `audit_log`. They deliberately do **not** come
from `enforcement_outcomes`, which holds one row per (guild, user, pool) updated in place
— grouping its `attempted_at` by day would draw a confident chart of something that is
not true.
