# Phase 6 handoff — the rewrite is complete

Paste this into a fresh session to pick Timothy up after the rewrite.

---

We rewrote `banpool-tim-gcp` as **Timothy**, a Python service on Docker Compose with
SQLite, plus a React web UI. Read `CONTEXT.md` for the domain language, `PLAN.md` for the
plan, and `docs/adr/` for the eleven decisions behind it. Those three are the source of
truth; this handoff records what they don't. `docs/handoffs/phase-0.md` through
`phase-5.md` cover the scaffolding, the domain core, the API and enforcement, the bot and
the migration, and are still accurate except where noted below.

**Phase 6 is complete and committed. All six phases are done.** What remains is the
cutover itself and the two deferred performance changes, both below.

## What phase 6 built

**Backend.** OAuth login, browser sessions, and the three API additions the UI needed.
763 Python tests (was 647), 100% line and branch coverage on every module phase 6 touched.

**Frontend.** `web/` is now a real SPA: Vite, React 19, TypeScript, TanStack Router and
Query, Tailwind v4, a client generated from the backend's own OpenAPI document. 58 Vitest
tests against `msw`. Lint (`eslint`, type-aware, `strictTypeChecked`), type check, test
and build all run in CI as a separate job.

Nine screens: login, home, pools, one pool (listings, search, paging, bulk), user lookup,
your servers, one server (subscriptions, exceptions, notification channel, pause,
enforcement history), audit log, and operations.

## The decision the whole phase turned on

**A browser is a second kind of caller, not an exception to the first.**

ADR 0008 says every caller presents the internal token and names an actor in
`X-Timothy-Actor`. The obvious way to let a browser in — have nginx add the token — is
catastrophic: anyone who found the hostname could then send an administrator's user ID.

So the session cookie is a *credential in its own right*, and it names the actor itself.
There is nothing for a browser to assert and therefore nothing to forge. Sending
`X-Timothy-Actor` alongside a session is a 400, not an ignored header, because a client
that sends one is confused about which kind of caller it is.

`identity.py` is where both paths meet. A bearer credential that is *present* must be
correct even if a valid cookie is also there — falling back would hide a service whose
token has gone stale.

## ADR 0010, and the phase-5 debt it pays off

The OAuth `guilds` scope puts the caller's guild list on the session row, and for a
browser the membership scan is **the intersection of that with Timothy's guilds, and
nothing else**.

This is the carried-forward item phase 5 could not close: `is_member_of_any` cost a
Discord call per guild — 52 seconds across the real deployment — before it could refuse
somebody who was in none of them. A browser in none of Timothy's guilds is now refused
after **zero** Discord calls. Anyone in the intersection is still confirmed against
Discord, so the snapshot narrows the question and never answers it — somebody who left a
guild since logging in is still refused.

Two things fell out of it that are not obvious:

- **The permission cache could no longer key membership on the user alone.** "No" from a
  narrowed scan is only "no" about the guilds that scan covered, so a browser's narrow
  miss would have answered the bot's wide question for a whole TTL. The key is now
  `(user_id, frozenset(guilds_asked_about))`. Ordering is deliberately *not* in the key,
  so the bot's `X-Timothy-From-Guild` hint still shares one entry across every hint —
  and the scan still iterates the **list**, not the set. Iterating the set would have
  silently undone phase 5's `/list_pools` fix. `test_the_scan_still_asks_in_the_order_it_was_given`
  pins that.
- **A guild Timothy joins after you log in is invisible to you until you log in again.**
  Up to a week. The failure is a refusal and the fix is signing out and back in; the
  empty-list screens say so. This is the price of not holding a refresh token.

## Things worth knowing that are not in the docs

- **Timothy stores no Discord user tokens.** The access token is used three times at
  login — exchange, `/users/@me`, `/users/@me/guilds` — and discarded. It never acts as
  the user, so a stored token would be a credential with nothing to do and somewhere to
  leak from. There is no refresh story because there is nothing to refresh.
- **The `sessions.id` is the SHA-256 of the cookie, not the cookie.** Reading
  `timothy.db` gives you no sessions — which matters, because that file is also where the
  ban data is and people do open it with `sqlite3`.
- **`/auth/login` and `/auth/callback` are `include_in_schema=False`.** They are places a
  browser *navigates*, not calls, so they are deliberately absent from the generated
  client. A failed exchange redirects to `/?login=failed` rather than returning JSON,
  because this URL is somewhere a browser lands and a wall of JSON in the address bar is
  not an error message.
- **CSRF has two locks.** `SameSite=Lax` is the first; the second is an `Origin` check on
  every session-authenticated non-GET. The second exists because the first is a browser
  default that a future embedding or a stray `SameSite=None` could turn off silently, and
  the failure would be total.
- **Bulk listing writes one audit row per listing, flagged `bulk: true`.** A single
  summary row would have made "why is this user listed, and who did it" unanswerable for
  every user added in a batch, which is the question the log exists for.
- **`ENFORCEMENT_BURST_LIMIT` will trip on any real bulk listing.** That is the intended
  trade (ADR 0007) and the confirmation dialog says so in words before the button works.
- **The listing search escapes LIKE wildcards.** A moderator typing `alt_account` is
  searching for a string; unescaped, `_` is "any character" and matches everything.
- **`openapi-fetch` binds `globalThis.fetch` at `createClient` time.** The client passes
  `fetch: (r) => globalThis.fetch(r)` so it resolves per call. Without it the tests make
  real network calls to a port nobody is listening on, and the symptom is `ECONNREFUSED`
  rather than anything about interception.
- **`web/src/api/schema.d.ts` is generated and committed**, and CI regenerates it and
  fails if the committed copy drifted. That is the web half of what
  `apps/bot/tests/test_contract.py` does for the bot — for the client that has no Python
  to assert it in. Run `npm run api` in `web/` whenever the API changes.

## Two departures from PLAN.md, both deliberate

**shadcn/ui is not installed.** `web/src/components/ui.tsx` is written in its idiom — the
same Tailwind vocabulary, the same `cn` helper, the same prop shapes — without Radix.
What shadcn actually brings to screens like these is Radix behind the interactive
components, and the interactions here are a `<select>`, a table and one confirm dialog.
The classes are compatible, so `shadcn add` later drops in over the top. PLAN.md's Stack
table records this.

**Playwright is not set up.** The stack table names it and there is nothing browser-driven
in CI. What an end-to-end test would actually exercise past the Vitest suite is the OAuth
round trip, and that needs a real Discord application, a real consent screen and a real
person clicking it — none of which CI has. The seam is instead covered from both sides:
`apps/api/tests/test_auth.py` drives the real flow against a fake Discord (both legs, the
state cookie included), and the CI stack job asserts the built bundle is served, a browser
with no session gets a 401, and login fails closed when unconfigured. **This is the
largest untested gap in phase 6** and it is named as such below.

## The operations view

`/ops` in the UI, `GET /ops/*` on the API, gated on **`TIMOTHY_OWNER_IDS`** — whoever runs
the deployment, usually one Discord ID (ADR 0011).

It was first gated on the management guild's administrators, and that was the wrong
reading of who those people are: administering the management guild makes somebody
responsible for the *pools*, not for the deployment, and a guild can have several
administrators. `OWNER_IDS` is the first requirement in the table that is not derived from
Discord, and ADR 0011 draws the line between it and the in-app RBAC ADR 0001 rejected —
briefly, it is configuration sitting beside `MANAGEMENT_GUILD_ID` rather than a stored
grant, it only ever narrows, and it appears against exactly one operation.

Unset closes `/ops` for everybody and never falls back. A mistyped ID does the same, and
the symptom is a 403 rather than an error — that is the first thing to check if the
Operations tab goes missing.

It was built for the cutover specifically. Every panel answers a question somebody asks at
2am: is dry run still on, did the workers stop, how far through the sweep are we, which
server is producing all the failures, what is stuck in the queue.

Three things about it are load-bearing:

- **Counts over time come from `audit_log` and nowhere else.** `enforcement_outcomes` is
  one row per (guild, user, pool) *updated in place* — its `attempted_at` is the latest
  attempt, not a history. Grouping it by day produces a plausible chart of something that
  is not true. Outcomes appear only as totals, which is what that table can honestly
  answer.
- **Dry-run activity splits on what it *would* have done.** The engine writes
  `detail.would` = `ban` or `warn`, and the activity series carries that through as
  `enforcement.dry_run:ban` / `:warn`. A bare dry-run count cannot answer "how many bans
  would that have been", which during a cutover is the only question.
- **There is no retry button on the queue, on purpose.** A job reaches `failed` only after
  exhausting its attempts on something running it again would not fix — an unknown kind, a
  payload missing its key. The failures worth retrying are recorded as enforcement
  outcomes and picked up by the sweep. A retry button would reliably do nothing.

The overview also flags two things that are otherwise invisible: `workers_enabled` off
(the API serves, every healthcheck passes, and nothing is being enforced) and
`login_configured` false (the stack is up and nobody new can sign in).

## Routes added

```
GET    /auth/login                        → 307 to Discord (not in the schema)
GET    /auth/callback                     → 303 to / or /?login=failed (not in the schema)
GET    /auth/me                           → who you are, and manages_pools
POST   /auth/logout                       → 204

GET    /guilds                            → the guilds you administer
GET    /pools/{name}/listings             → now a page: limit, after_id, q
POST   /pools/{name}/listings/bulk        → many at once
POST   /pools/{name}/listings/bulk-delete → many at once, optional ?revert

GET    /ops/overview                      → settings, counts, queue, outcomes
GET    /ops/activity?days=                → per-UTC-day counts from the audit log
GET    /ops/failures                      → enforcement failures grouped by guild + cause
GET    /ops/jobs                          → the queue, filterable by status and kind
```

`GET /pools/{name}/listings` **changed shape** from a list to
`{listings, next_after_id, total}`. Nothing but the API's own tests read it — the bot has
no such call — but it is the one breaking change in this phase.

## Carried forward

> **Superseded by [`post-rewrite.md`](./post-rewrite.md)**, written after the staging
> deployment went up. The list below is what was known when phase 6 was declared complete;
> the newer one has what bringing it up actually found. Everything above this line is still
> accurate.

- **Nothing has been migrated yet.** Unchanged from phase 5: the real dump, the real
  snapshot and the real cutover are an operator running `docs/cutover.md`. `verify` and
  `diff` have been rehearsed against production data but the cutover has not happened.
- **The internal token has no rotation story (ADR 0008), and the production one was
  pasted into a session transcript during the phase-5 rehearsal. Rotate it.** Still
  outstanding, and now more so: there is a second secret beside it, the OAuth client
  secret, and neither has a rotation path.
- **No end-to-end test of the OAuth round trip**, above.
- **`diff` has no end-to-end exercise in CI**, unchanged from phase 5.
- **A guild that removes Timothy while the bot is offline stays registered**, unchanged.
- **GitHub Actions layer caching**, still deferred. Comment marks the spot in `ci.yml`,
  and there are now four jobs rather than two.
- **Guild names are never shown, only IDs.** Timothy's `guilds` table holds the ID and
  nothing else, and fetching a hundred names from Discord to decorate a list would spend
  the rate-limit budget enforcement runs on. Doing this properly means caching names, and
  that is a decision, not an afternoon.
- **`enforcement_outcomes` has no index on `status`.** `/ops/overview` counts by status,
  which is a full scan of that table. It is small today and a scan of tens of thousands of
  rows on SQLite is a few milliseconds; add the index when the dashboard feels slow, not
  before.
- **`audit_log` grows forever and nothing prunes it.** `/ops/activity` is bounded by its
  window and the table is indexed on `at`, so this is a disk question rather than a query
  question — but it is the first thing that will want a retention policy, and that policy
  is a decision about how far back "why is this user banned" has to reach.
- **`GET /guilds` costs a resolved permission per candidate guild.** Cheap for a browser
  (its snapshot is a handful) and expensive for a service caller with no snapshot, which
  is why the bot has no command for it. Fine as it stands; worth remembering before
  anything else calls it.

## The two deferred performance changes, unchanged and still in this order

**1. Sweep guilds concurrently.** 48 hours → ~2.2 hours, measured in phase 5. No new
`DiscordPort` operation, no privileged intent — the worker simply holds more than one job
at a time. The care it needs is on the write side: concurrent jobs mean concurrent writes
to SQLite as sole writer, the breaker's per-run accounting is per job, and shutdown has to
stay clean. Do this first, once the system has been running long enough to be boring.

**2. Bulk member listing.** `GET /guilds/{id}/members?limit=1000` instead of one lookup
per candidate — 347,407 requests down to ~1,230 for guilds of 10k. It needs a sixth
`DiscordPort` operation (ADR 0007 keeps that surface at five on purpose), it makes the
privileged `GUILD_MEMBERS` intent load-bearing for enforcement, and it changes what a
sweep *is*. Deserves its own ADR. Worth doing before the deployment grows.

Phase 5's handoff has the full arithmetic for both.

## What to do next

The rewrite is done. The next thing is not code:

1. **Rotate the internal token**, generate the OAuth client secret fresh, and set
   `TIMOTHY_OWNER_IDS` to your own Discord user ID — without it `/ops` is closed, which
   is the page you will want open during the cutover.
2. **Run `docs/cutover.md`** top to bottom. It is written to be executed by somebody who
   has not read the code.
3. **Let it run.** Then do the sweep concurrency work, with the system boring enough that
   a regression is visible.
