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

### Themes

Two families — **default** and **industrial** — each in light and dark, chosen from two
selects in the top bar and stored in the browser. Mode also offers **system**, which is
the behaviour the app had before it had a selector.

`web/src/styles.css` is the whole of it. Tokens are declared once in a single top-level
`@theme`; each theme overrides them as ordinary custom properties under
`html[data-family=…][data-mode=…]`. That arrangement is not incidental — `@theme` nested
inside a `@media` block or a selector is **not** scoped to it. Tailwind hoists the
declarations and the last one silently wins, which is how this file spent its first months
defining a light palette that never rendered a pixel. `src/test/styles.test.ts` compiles
the real stylesheet and asserts the four themes still resolve to four different palettes,
because no test running under `css: false` can see that they do not.

`data-mode` on `<html>` is always `light` or `dark`; "system" is a stored preference the
inline script in `index.html` resolves before the first paint. That script is a deliberate
duplicate of `src/components/theme.ts` — nothing imported by the bundle can run early
enough to prevent a flash — and the two must be kept in step. It also means a
Content-Security-Policy, if one is ever added to `web/nginx.conf`, needs a hash or a nonce
for it.

The industrial family is designed against **Berkeley Mono**, which is licensed and is in
neither this repository nor any image built from it. Symlink your own copy in for local
work and set `TIMOTHY_BERKELEY_MONO_DIR` to serve it from a deployment — see
`web/public/fonts/berkeley/README.md`. Without it the theme renders in IBM Plex Mono,
which is committed under the SIL OFL, and that is the only font CI has ever seen.

## Running

```sh
cp .env.example .env  # every variable there is commented; the tokens are required
docker compose up --build
```

No service publishes a port. The Cloudflare Tunnel is the only ingress and its single
origin is `http://web:80`, which serves the SPA and proxies `/api` to the backend.

### Setting up the tunnel

You need a domain whose nameservers already point at Cloudflare. The tunnel is
*remotely managed* — `compose.yaml` runs `cloudflared tunnel run` with a token and no
local config file, so everything below is done in the dashboard and nothing is committed.

1. **Zero Trust dashboard** → Networks → Tunnels → **Create a tunnel** → **Cloudflared**.
   Name it (`timothy` is fine) and save.
2. Cloudflare shows an install command with a long token in it. You want **only the
   token** — the part after `--token`. Put it in `.env`:

   ```sh
   CLOUDFLARE_TUNNEL_TOKEN=eyJhIjoi...
   ```

   Ignore the rest of the install command; the container is already in `compose.yaml`.
3. On the tunnel's **Published application routes** tab, add one route:

   | Field | Value |
   | --- | --- |
   | Subdomain | `timothy` (or whatever you like) |
   | Domain | your domain |
   | Type | `HTTP` |
   | URL | `web:80` |

   `web` is the compose service name — cloudflared resolves it on the compose network, so
   this stays internal and no port is ever published. **Type is `HTTP`, not `HTTPS`:** TLS
   is terminated by Cloudflare at the edge, and nginx inside the network speaks plain
   HTTP.

   Leave the extra settings alone. In particular do **not** set an `HTTP Host Header`
   override — Timothy's CSRF check compares the browser's `Origin` against the `Host` it
   received, and rewriting the host would refuse every state-changing request from the UI.
4. Cloudflare creates the DNS record for you. Set the matching base URL in `.env`:

   ```sh
   TIMOTHY_PUBLIC_BASE_URL=https://timothy.yourdomain.com
   ```
5. `docker compose up --build`. `cloudflared` connects outbound, so there are no inbound
   firewall rules and nothing to forward.

Because the tunnel terminates TLS, the session cookie's `Secure` flag works and
`TIMOTHY_SESSION_COOKIE_SECURE` can stay at its default of `true`.

Optionally you can put a Cloudflare Access policy in front of the hostname for a second
layer in front of everything. It composes fine — the Discord login is a browser navigation
and a signed-in Access session carries through the callback — but it is a separate login
to get past, so it is worth deciding deliberately rather than by accident.

### The web UI's login

`TIMOTHY_DISCORD_CLIENT_ID`, `TIMOTHY_DISCORD_CLIENT_SECRET` and
`TIMOTHY_PUBLIC_BASE_URL` turn on browser login. Leaving them unset closes it — the stack
comes up and `/api/auth/login` answers 503 naming what is missing.

**Only members of `TIMOTHY_MANAGEMENT_GUILD_ID` can sign in** (ADR 0013). Membership is
the whole test — no role and no permission there is required — and anybody else who
completes the Discord consent screen lands back on the login page being told so, with no
session issued. That includes whoever runs the deployment: `TIMOTHY_OWNER_IDS` is what
opens `/ops`, but reaching it in a browser means being in the management server. An unset
`TIMOTHY_MANAGEMENT_GUILD_ID` therefore closes login as well, with the same 503.

On the Discord application, under **OAuth2 → Redirects**, add the redirect URI **exactly**
as `<TIMOTHY_PUBLIC_BASE_URL>/api/auth/callback`. Discord compares the string, and a
trailing slash is a failed login with no useful message. The backend requests
`identify guilds`; nothing else.

> **Not the Interactions Endpoint URL.** The application's General Information page has a
> field of that name, and it is a different thing entirely — it is where Discord would
> POST slash-command interactions if you wanted to handle them over HTTP instead of the
> gateway. Putting the callback there does two bad things: Discord validates the field by
> POSTing a signed ping, gets a `405` from a GET-only OAuth route, and refuses to save it;
> and if it ever *did* save, the bot would stop receiving commands over the gateway
> altogether. `discord.client` warns about this on startup — "Application has an
> interaction endpoint URL set, this means registered components and app commands will not
> be received by the library". Leave that field empty; Timothy uses the gateway.

Timothy must also be invited with the **`applications.commands`** scope, not just `bot`.
Without it, uploading the pool commands to the management guild answers
`403 Missing Access` — the bot logs which setting to check and carries on. An invite URL
with everything Timothy needs:

```
https://discord.com/oauth2/authorize?client_id=<client id>&scope=bot+applications.commands&permissions=3076
```

`3076` is View Channel, Send Messages and Ban Members — the three the
[Discord port](./packages/core/src/timothy_core/ports/discord.py) can actually use.

Timothy stores no Discord user tokens. The access token is used once, at login, to ask who
you are and which servers you are in, and then discarded — what you may *do* is resolved
with the bot token on every request (ADR 0001, ADR 0010).

### Trying it out before the cutover

The old bot is still running on the same Discord application, so a local stack pointed at
the same token is not a sandbox — it is a second instance of a live bot. Three settings
decide whether that is safe, and the defaults are not the safe ones:

| Setting | Set to | Because |
| --- | --- | --- |
| `TIMOTHY_SYNC_COMMANDS` | `false` | On startup the bot **replaces** the application's slash commands. With the default `true` it overwrites the surface real moderators are using right now. |
| `TIMOTHY_GATEWAY_ENABLED` | `false` | Otherwise you have two gateway sessions on one token, both receiving `GUILD_MEMBER_ADD`. |
| `TIMOTHY_WORKERS_ENABLED` | `false` | Otherwise the sweep scheduler starts immediately and begins working through every guild, at two Discord calls a second, for days. |

Keep `TIMOTHY_DRY_RUN=true` as well. It is the default and it fails safe, but it is the
thing standing between a test click and a real ban.

The cleanest option is a **separate Discord application** — its own bot invited to one
test server — which removes all of the above. The token is the only thing that has to
differ.

With the gateway off, nothing registers guilds, so `/guilds` starts empty. Register one by
hand — it is also a decent end-to-end check of the tunnel, nginx and the backend in one
call:

```sh
curl -X PUT https://timothy.yourdomain.com/api/guilds/<guild id> \
     -H "Authorization: Bearer $TIMOTHY_INTERNAL_TOKEN" \
     -H "X-Timothy-Actor: system"
```

Then sign in at `https://timothy.yourdomain.com`, from an account that is in the
management server — anybody else is refused at the door (ADR 0013). What you can see
depends on real Discord
permissions: the server pages need `ADMINISTRATOR` in that server, the pool pages need one
of the roles in `TIMOTHY_POOL_MANAGER_ROLE_IDS` held in `TIMOTHY_MANAGEMENT_GUILD_ID`, and
`/ops` needs your user ID in `TIMOTHY_OWNER_IDS`. Administering the management server is
deliberately not enough for the pool pages (ADR 0012) — if the Pools tab is missing, check
that you hold the role and that the variable names it.

Two things to be aware of: the tunnel hostname is a **public URL**, not localhost — anyone
who finds it reaches the login page, which is what the internal token and the session are
there for. And the stack writes to a Docker volume, so `docker compose down -v` is how you
throw the test database away.

### When Discord says `50001 Missing Access`

Uploading commands to the management guild fails with that error for three different
reasons, and the error cannot tell them apart. Ask Discord instead — read-only, and it
picks up the running stack's own environment:

```sh
docker compose run --rm --no-deps -T --entrypoint python bot - \
    < scripts/check-discord-access.py
```

It prints which application the token belongs to, every guild the bot is actually in, and
whether the application is authorised for commands there. That last one is the
`applications.commands` **scope**, which is granted by the invite URL and is *not* one of
the bot's permissions — which is why re-inviting with the same link changes nothing.

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
and the pool and listing commands exist only in `TIMOTHY_MANAGEMENT_GUILD_ID`. Both ship
administrator-only and unavailable in DMs, which is Discord deciding who *sees* them; you
can point the pool commands at the pool manager role instead, under Server Settings →
Integrations. Either way the backend resolves the caller against Discord and enforces the
real rule on its own account — the pool commands need the role, not `ADMINISTRATOR`
(ADR 0012).

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
Discord user ID. Managing the pools makes somebody responsible for the lists, not for
Timothy itself, so this is a separate and much smaller list (ADR 0011). Unset closes the
page for everybody; it never falls back.

It answers the questions that come up during a cutover and afterwards: is dry run still on, are the
workers running, how far through the sweep is it, which server is producing all the
failures, and what is stuck in the queue.

Counts over time come from the append-only `audit_log`. They deliberately do **not** come
from `enforcement_outcomes`, which holds one row per (guild, user, pool) updated in place
— grouping its `attempted_at` by day would draw a confident chart of something that is
not true.

### Logs

Every service writes into `./logs/` on the host, and the files survive the containers
being rebuilt or recreated (ADR 0014):

```
logs/backend.log      JSON lines — the API, the worker, the sweeper, uvicorn
logs/bot.log          JSON lines — the gateway client and the relay
logs/web-access.log   nginx, one line per request
logs/web-error.log    nginx
logs/cloudflared.log  the tunnel
```

`docker compose logs` still works and shows the same thing for the current containers;
the difference is that these outlive them, which is what makes "when did this start?"
answerable.

Every file rolls at 10MB and keeps nine behind the live one, so each tops out at 100MB and
the directory needs no attention. Nothing on the host does that — the Python services
rotate themselves, nginx runs `logrotate` inside its own container, and cloudflared
rotates its own.

Everything a process is unhappy about is at `ERROR` or above, and the JSON lines are
built to be filtered:

```bash
# Every error anywhere in the stack, most recent last.
grep -h '"level":"ERROR"' logs/backend.log logs/bot.log

# Just the tracebacks, readable.
jq -r 'select(.exception) | "\(.ts) \(.logger)\n\(.exception)"' logs/backend.log

# What happened to one guild.
grep 100000000000000002 logs/*.log
```

A React crash in the web UI is posted to the backend by the SPA and appears in
`backend.log` under the logger `timothy.web`, with the component stack in
`extra.client_stack` — a blank page in somebody's browser leaves a record on the host.

**Credentials are stripped before anything is written.** The bot token, the internal
token and the OAuth client secret are registered by exact value, and labelled secrets
(`token=`, `Authorization: Bearer`, `?code=`) are caught by shape on top of that. nginx
logs the route without its query string, so an OAuth code never reaches the file at all.
It is worth grepping a log for a token you know before pasting it anywhere — but if
something did get through, that is a bug in `packages/logs`, not something to work
around.

Turn the files off with an empty `TIMOTHY_LOG_DIR`; logging falls back to stdout only.
