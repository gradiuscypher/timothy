# Where Timothy stands, and what is left

Paste this into a fresh session to pick Timothy up. It supersedes the "Carried forward"
section of `docs/handoffs/phase-6.md`; everything else in that document is still accurate
and is where the *why* of phase 6 lives.

---

Timothy is a rewrite of `banpool-tim-gcp` — a Python service on Docker Compose with
SQLite, a Discord bot, and a React web UI. `CONTEXT.md` has the domain language, `PLAN.md`
the plan, `docs/adr/` the eleven decisions. `docs/handoffs/phase-0.md` through `phase-6.md`
cover how it was built.

**All six phases are complete. The code is done and running on staging. Nothing has been
migrated.** What is left is a cutover and a short list of deliberate deferrals.

## State

- 766 Python tests, 58 Vitest tests, `ruff` / `ty` / `eslint` / `tsc` clean.
- CI has four jobs: Python, the SPA, the generated-client contract, and a stack that
  builds every image and asserts against a running deployment.
- **A staging deployment is up and working** — staging bot, staging guild, staging
  application, behind its own Cloudflare Tunnel hostname. That is the first time the whole
  thing has run end to end outside CI.

## The one thing standing between here and production

**Run `docs/cutover.md`.** It is written to be executed top to bottom by somebody who has
not read the code, and the tooling behind it has been rehearsed against the real
production dump: `verify` covered all 123 guilds and 378,348 pairs with zero unexplained
findings. What has never happened is the cutover itself.

Before starting it, four things need doing that the runbook assumes are already true:

1. **Rotate `TIMOTHY_INTERNAL_TOKEN`.** The production one was pasted into a session
   transcript during the phase-5 rehearsal. This has been outstanding for two phases and
   is the only item here with a security clock on it.
2. **Create the production OAuth credentials** — client ID and secret on the *production*
   Discord application, which is a different application from staging. Register the
   redirect URI under **OAuth2 → Redirects** as `<PUBLIC_BASE_URL>/api/auth/callback`, and
   leave the **Interactions Endpoint URL** field empty (see below).
3. **Set `TIMOTHY_OWNER_IDS`** to your Discord user ID, or `/ops` is closed to everybody
   — and `/ops` is the page you will want open during the cutover.
4. **Set up the production Cloudflare Tunnel** and its hostname route. README has the
   walkthrough.

## What bringing staging up actually taught us

Three things, all of which cost real time and none of which any test could have caught.

**The Interactions Endpoint URL is not the OAuth redirect URI.** Putting the callback in
that field — on the application's General Information page — makes Discord POST a signed
ping to a GET-only route, get a `405`, and refuse to save the field. Worse, had it saved,
the bot would have stopped receiving slash commands over the gateway entirely;
`discord.client` warns about exactly this on startup. README and `.env.example` now say so
in the strongest terms available.

**`applications.commands` is a scope, not a permission.** Uploading commands to the
management guild answers `403 Missing Access` without it, and the bot's permission integer
has nothing to do with it — which is why kicking and re-inviting changes nothing if the
invite URL is the same one as before. `scripts/check-discord-access.py` exists because of
this: it asks Discord which application the token belongs to, which guilds the bot is
really in, and whether commands are actually allowed there, which separates three causes
that all surface as the same error.

> **This particular failure should not recur in production**, and it is worth knowing why
> rather than being surprised either way. The subscribing guilds already run the old bot's
> global slash commands, which is only possible if the application was authorised with
> `applications.commands` there. The management guild is the one to check by hand, since
> the old bot registered its commands through the retired `slash_cli` rather than through
> this path. Run the script against production before cutover morning.

**A refused command sync used to kill the bot.** It propagated out of `setup_hook`, the
container restarted into the same failure forever, and each loop re-uploaded the global
commands against Discord's rate limit — trading a missing slash command for no gateway at
all, which is the *primary* enforcement path (ADR 0004). It also crashed before
`on_ready`, so the one log line that would have diagnosed it never printed. Now it logs
which settings to check and carries on. Fixed in `ad6f836`.

## Deferred, in the order I would do them

**1. Sweep guilds concurrently.** 48 hours per round → ~2.2 hours, measured in phase 5. No
new `DiscordPort` operation and no privileged intent; the worker simply holds more than one
job at a time. The care it needs is on the write side — concurrent writes to SQLite as sole
writer, per-job breaker accounting, and a clean shutdown. **Do this once the system has
been running long enough to be boring**, not before.

**2. Bulk member listing.** `GET /guilds/{id}/members?limit=1000` instead of one lookup per
candidate: 347,407 requests down to ~1,230 for guilds of 10k. Needs a sixth `DiscordPort`
operation (ADR 0007 holds that surface at five on purpose), makes the privileged
`GUILD_MEMBERS` intent load-bearing for enforcement, and changes what a sweep *is*.
Deserves its own ADR. Phase 5's handoff has the full arithmetic.

**3. An end-to-end test of the OAuth round trip.** The largest untested gap in the project.
Both sides are covered — `apps/api/tests/test_auth.py` drives the real flow against a fake
Discord, and the CI stack job proves the bundle is served and login fails closed — but
nothing exercises the seam. It needs a real Discord application and a real consent screen,
which is why it was deferred. A staging deployment now exists, which makes a Playwright run
against it more plausible than it was.

**4. GitHub Actions layer caching.** Four jobs now rather than two, and the stack job still
rebuilds every image from scratch. A comment marks the spot in `ci.yml`.

## Smaller things, none of them blocking

- **`enforcement_outcomes` has no index on `status`.** `/ops/overview` counts by status,
  which is a full scan. Milliseconds today; add the index when the dashboard feels slow.
- **`audit_log` grows forever and nothing prunes it.** The first thing that will want a
  retention policy — and that policy is a decision about how far back "why is this user
  banned" has to reach, not a chore.
- **Guild names are never shown, only IDs.** The `guilds` table holds the ID and nothing
  else, and fetching a hundred names to decorate a list would spend the rate-limit budget
  enforcement runs on. Doing it properly means caching names.
- **`GET /guilds` costs a resolved permission per candidate guild.** Cheap for a browser,
  whose ADR 0010 snapshot is a handful; expensive for a service caller with no snapshot,
  which is why the bot has no command for it.
- **`is_member_of_any` still costs a full scan for a service caller.** ADR 0010 closed this
  for browsers only. The bot's mitigation is still `X-Timothy-From-Guild`, which reorders
  the scan but does not shorten it for a genuine non-member.
- **A guild that removes Timothy while the bot is offline stays registered.** Unchanged
  since phase 4. Nothing prunes `guilds` after import either.
- **`diff` has no end-to-end exercise in CI.** Unchanged since phase 5.
- **`scripts/check-discord-access.py` is a script, not a command.** It earned its place
  debugging the staging bring-up and will earn it again at cutover. Promoting it to a
  `timothy-doctor` entry point alongside `timothy-migrate` would be an afternoon, and would
  be the natural home for other pre-flight checks (is dry run on, is the token valid, does
  the management guild resolve).
- **`/ops` is read-only, deliberately.** If it ever grows a write — resume a paused guild,
  flip dry run — that wants its own ADR. ADR 0011's case for a single configured owner
  rests partly on there being nothing there to break.

## Two decisions worth not re-litigating

**ADR 0010** — a browser session carries the OAuth `guilds` snapshot, which narrows the
membership scan to the intersection with Timothy's guilds. It never *answers* the question,
only narrows it, and the permission cache keys on the set that was scanned so a browser's
narrow miss cannot answer the bot's wide question.

**ADR 0011** — `/ops` is gated on `TIMOTHY_OWNER_IDS`, not on the management guild's
administrators. Administering that guild makes somebody responsible for the pools, not for
the deployment. Unset closes the view for everybody and never falls back.
