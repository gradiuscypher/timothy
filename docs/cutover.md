# Cutover runbook

Moving the live deployment from `banpool-tim-gcp` (Rust, MongoDB, GCP) to Timothy. Run
top to bottom. Every step is either reversible or repeatable, and the two that are
neither — switching the old bot off, and switching dry run off — are called out where
they happen.

The tool is `timothy-migrate`, in `migration/`. It has four subcommands and they are the
order of this document.

> Read [ADR 0002](adr/0002-global-pool-becomes-opt-out.md) and
> [ADR 0009](adr/0009-dry-run-records-intentions-not-attributions.md) first if you have
> not lately. They are the two decisions the whole cutover turns on: `global` stops being
> a hardcoded short-circuit and becomes rows, and dry run leaves its evidence in the audit
> log rather than in `enforcement_outcomes`.

## What you need

- A `mongodump` of the production database.
- The production bot token, in `TIMOTHY_DISCORD_TOKEN`.
- A checkout with `uv sync` run, or the same commands inside a container.
- Somewhere to keep the artefacts. Everything below writes files; keep all of them,
  because together they are the record of what the cutover did.

## 1. Take the dump

```sh
mongodump --uri "$MONGODB_URI" --db banpool --out ./cutover/dump
```

The importer never connects to Mongo — it reads the `.bson` files. That is what makes the
import repeatable, and repeatable is what makes a rehearsal evidence about the real run
rather than a separate event with its own timing.

Both `./cutover/dump/*.bson` and `./cutover/dump/banpool/*.bson` are understood.

`adminroles` and `serverconfig` are in the dump and are deliberately not imported —
neither has had a live caller in years, and their slash commands are in the old
repository's `json_commands/archive/`. The report says so rather than staying quiet.

## 2. Snapshot the guild list

```sh
export TIMOTHY_DISCORD_TOKEN=...
uv run timothy-migrate guilds --output ./cutover/guilds.json
```

The only step that touches the network. It asks Discord `GET /users/@me/guilds` and
writes what it got.

**Why this is not derived from the dump.** Mongo never had a guild table. A guild appeared
in `subscriptions`, `exceptions` or `notifications` only once somebody configured
something there — and the guilds this cutover most exposes are exactly the ones that
configured *nothing* and rode the implicit `global`. Deriving the list from the dump would
drop them, and they would leave the shared banlist without anyone asking them.

Open the file. It is a list of guild names, and you know what this deployment looks like.
A short one means the token is wrong or the fetch was truncated.

## 3. Import

```sh
uv run timothy-migrate import \
  --dump ./cutover/dump \
  --guilds ./cutover/guilds.json \
  --database ./cutover/timothy.db \
  --report ./cutover/import.json
```

Add `--dry-run` first if you want the report without the database; it prints exactly what
the real run will print.

`--global-pool` defaults to `global` and **must match the backend's
`TIMOTHY_AUTO_SUBSCRIBE_POOL`**. If they disagree, guilds that join after cutover get a
different default from every guild that was there before.

### Reading the report

Four sections.

**Read from the dump / Written to SQLite.** Counts. `listings` will be smaller than
`bans`: the old `delete_pool` deleted one document and cascaded nothing, so every pool
ever deleted left its listings behind, enforced by nothing and visible to no command.

**Documents that could not be imported.** Rows that cannot become valid rows — a
`user_id` that is not a number, a `subscription_level` that is neither `ban` nor `warn`.
They are kept, with the reason and the document, in `import.json`. Nothing is coerced: an
unreadable level is not defaulted to `ban`, because that would ban people on the strength
of a typo, and not to `warn`, because that would quietly stop enforcing a pool a guild
believes it is enforcing.

**Decided during the import.** Duplicates resolved, orphans dropped, guilds Timothy has
left, and the count of `global` subscriptions materialised. Read this one properly. It is
every decision the import took on your behalf.

**What was deliberately not written.** `enforcement_outcomes` is empty. It has to be: an
outcome is Timothy's claim to have issued a ban *itself*, and every ban standing in these
guilds today was the old bot's. Inventing outcomes for them would arm the revert path
against thousands of bans Timothy never placed, and the first unsubscribe after cutover
would lift them all. Timothy takes attribution when it issues a ban, and not before.

### If you need to re-run it

Import into a new file. The import assigns pool IDs from scratch, so it is not idempotent
and there is no partial re-run; it refuses a database that already holds data rather than
producing one where half the listings point at the wrong pools.

## 4. Verify

```sh
uv run timothy-migrate verify \
  --dump ./cutover/dump \
  --database ./cutover/timothy.db \
  --report ./cutover/verify.json
```

This is the static check, and it is complete. For every guild in the imported database
crossed with every user listed on either side, it asks both systems what they would do —
Timothy through the real `decide()` the backend runs, the old bot through a transcription
of `is_user_banned_on_guild` — and compares.

Four verdicts:

| Verdict | What it means | Expected? |
| --- | --- | --- |
| `agrees` | Both would ban, or neither would act. | The overwhelming majority. |
| `now warns instead of banning` | The guild holds the pool at `warn`. The old bot's live check never read the level and banned anyway; Timothy honours it. | **Yes.** See below. |
| `no longer enforced` | The old bot would ban and Timothy will not. | **Yes, if** the reason given is a pool that no longer exists. |
| `newly enforced` | Timothy would act where the old bot would not. | **No. Never.** |

Exit code `0` if `newly enforced` is empty, `2` if it is not.

**`newly enforced` must be zero.** There is no case where this migration should produce
one. A non-zero count means the import invented a subscription, invented a listing, or
lost an exception. Stop; the findings name the guild and the user.

**Agree to the other two by hand.** They are real behaviour changes:

- *now warns* — those guilds have been getting bans they did not ask for. Timothy stops.
  If a guild set `warn` years ago and has been banning ever since, its moderators may be
  surprised in either direction; that is a conversation, not a bug.
- *no longer enforced* — those guilds have been enforcing a pool that was deleted. The
  listings still existed, `is_guild_subscribed` still matched the dead name, and the bans
  still landed. Timothy stops that too.

If either count is larger than you expected, `verify.json` has every finding, untruncated.

## 5. Rehearse in dry run

Put the imported database where the backend will find it, and bring the stack up with dry
run **on** and the old bot **still running**. Nothing about this step is exclusive; both
bots can watch the same guilds, because Timothy issues nothing.

**Start with the workers off, put the database in place, then turn them on.** Starting
first and swapping afterwards means restarting mid-sweep, which strands a running job and
queues a duplicate for that guild. Two steps avoid it:

```sh
TIMOTHY_WORKERS_ENABLED=false docker compose up -d --wait backend

docker compose cp ./cutover/timothy.db backend:/data/imported.db
docker compose exec -T -u root backend sh -c '
  rm -f /data/timothy.db /data/timothy.db-wal /data/timothy.db-shm
  mv /data/imported.db /data/timothy.db
  chown timothy:timothy /data/timothy.db
'

TIMOTHY_WORKERS_ENABLED=true docker compose up -d --force-recreate --wait backend
```

`--force-recreate` is needed for the second start: `docker compose restart` reuses the
container's original environment and would leave the workers off.

**Run the backend alone.** Do not start `bot` for the rehearsal. It uploads the slash
command tree on startup, which would overwrite the live application's command surface, and
it would open a second gateway connection alongside the old bot. The sweep lives in the
backend and uses REST only.

Check `.env`:

- `TIMOTHY_DRY_RUN=true` — it fails safe, so anything unreadable also means on.
- `TIMOTHY_AUTO_SUBSCRIBE_POOL` matches the `--global-pool` you imported with.
- `TIMOTHY_MANAGEMENT_GUILD_ID` is the real one.
- `TIMOTHY_POOL_MANAGER_ROLE_IDS` names a role that exists in that guild, and the people
  who will run `/add_ban` on cutover day actually hold it. Administering the management
  guild is not enough (ADR 0012), and unset means nobody can list anybody — a 403 on the
  first `/add_ban`, which is a bad thing to discover mid-cutover.
- `TIMOTHY_SYNC_COMMANDS` — irrelevant here, since the bot is not running.

### How long a round actually takes

Longer than you will guess. A guild sweep is one `fetch_member` per candidate, and Discord
paces those at roughly **two per second per guild** — measured, not estimated. A guild with
3,000 listed users therefore takes around half an hour, and jobs run one at a time.

For the production data that is **~347,000 lookups, about 48 hours** for one full round.
Rehearse against a *subset*: trim `guilds.json` to a handful of representative guilds,
import that to a separate database, and dry-run against it. `verify` has already covered
every guild statically, so what a subset gives up is fan-out volume, not correctness.

Set `TIMOTHY_SWEEP_INTERVAL` longer than a round takes, or rounds overlap.

### What dry run does and does not write

It writes `enforcement.dry_run` rows to the audit log. It writes **no `banned` and no
`warned` outcome** — that is the attribution ADR 0009 exists to withhold.

It *does* write `skipped_exception` outcomes, and that is correct: those say the guild
vouched for a user, which is true either way, and no revert can act on them because
reverting keys strictly on `banned`. **A non-zero `enforcement_outcomes` count is
therefore not on its own a sign that dry run is off.** Check the statuses:

```sh
docker compose exec -T backend python -c "
import sqlite3
print(dict(sqlite3.connect('/data/timothy.db').execute(
    'SELECT status, COUNT(*) FROM enforcement_outcomes GROUP BY status')))
"
```

Anything other than `skipped_exception` in dry run means dry run is not on. Stop.

Intentions do not dedupe — with no `banned` or `warned` row to settle a user, the same
pair is restated every round. Excepted users are the exception in both senses: they settle
and drop out.

## 6. Diff the rehearsal

```sh
uv run timothy-migrate diff \
  --dump ./cutover/dump \
  --database /var/lib/timothy/timothy.db \
  --report ./cutover/diff.json
```

Reads every intention the rehearsal recorded and classifies it against the old rule, with
the same verdicts as `verify`.

What this adds over `verify` is everything `verify` cannot reach: live guild membership,
live permissions, notification channels actually resolving, the worker and the sweep
really running.

**What it cannot show is silence.** A pair Timothy decided to skip writes nothing at all,
so under-enforcement is invisible here. That is exactly the gap `verify` closes, which is
why both are run and neither is optional.

If it reports *no* intentions, do not assume the rehearsal has not started. Check
`enforcement_outcomes` first — the other explanation is that dry run was off and Timothy
has been enforcing for real.

## 7. Cut over

In this order:

1. **Stop the old bot.** From here the two systems must not both be live: the old bot
   creates an exception on every unban, and Timothy's reverts would feed it.
2. `TIMOTHY_DRY_RUN=false`, restart the backend. This is the irreversible one.
3. Watch the first sweep. `enforcement_outcomes` starts filling with `banned` and
   `warned`; the audit log switches from `enforcement.dry_run` to `enforcement.ban` and
   `enforcement.warn`.
4. Watch for `enforcement.breaker_tripped`. `TIMOTHY_ENFORCEMENT_BURST_LIMIT` is 25
   enforcement actions — bans and notifications together — per guild per run, and the
   first real sweep after cutover is the run most likely to reach it in a guild that has
   drifted. A tripped breaker pauses that guild and asks for a human, which is what it is
   for: resume it deliberately, do not raise the limit to get past it.

   **The breaker covers the sweep and it covers subscribing, which is what matters here.**
   Both are many users in one guild, and each is a single run whose budget they share. It
   does *not* cover listing users one at a time or in bulk: each of those is its own run
   spending one action per guild, so a 500-entry bulk listing can land 500 actions in a
   guild without the limit ever being reached. During cutover nothing takes that path —
   the migration loads listings before enforcement is live, and the sweep is what acts on
   them — but do not read a quiet `enforcement.breaker_tripped` as proof that a bulk
   listing was safe.

### Every round takes about two days, so the sweep runs weekly

A round asks Discord "is this user in this guild?" once per listed user per subscribed
pool — ~347,000 lookups for this data. The worker holds one job at a time and awaits each
call, so they go out serially at about two a second: roughly **48 hours**.

That is not a cold start that pays off once. **It is every round.** Only `banned`, `warned`
and `skipped_exception` settle a candidate; a listed user who is merely *absent* records
nothing, on purpose, because settling them would have the sweep skip them forever if the
gateway later missed their join. Almost every candidate is absent — the rehearsal's guild
went 2,995 → 2,992 after a completed round.

So `TIMOTHY_SWEEP_INTERVAL` is **604800 (weekly)**, not the hour it used to be. That is
deliberate and it is not conservatism: an interval shorter than a round does not sweep more
often, because a guild with one outstanding is skipped. It only leaves the backend calling
Discord continuously and forever. Weekly is two days of work and five days quiet, on a
period that is actually the period.

**The safety net is therefore a weekly net.** Set your expectations of it accordingly — and
note that none of this touches the primary path: a listed user who joins is banned at the
door by the gateway event, immediately, and that path never consults the candidate set
(ADR 0004). The sweep is a backstop for gateway outages, not how bans normally happen.

The two-a-second is serial issuance, not Discord's pacing — separate guilds have separate
rate-limit buckets, and 30 concurrent lookups across 30 guilds measured 43/s, which would
put a round at a couple of hours. Sweeping guilds concurrently is the fix and is
deliberately **not** being done before the cutover: it means concurrent writes to a
single-writer SQLite database, which deserves its own pass rather than a rushed one. See
PLAN.md, "What a sweep costs".

### If it goes wrong

Set `TIMOTHY_DRY_RUN=true` and restart. Timothy stops acting immediately. Bans already
issued stay — reverting them is a decision, not a rollback, and
`POST /guilds/{id}/enforcement` with `paused: true` is the narrower tool if it is one
guild.

The database is a file. Keep `./cutover/timothy.db` as it was written, and the artefacts
from steps 2–6 with it.

## Afterwards

- Guilds that removed Timothy while the old bot was running are not in the snapshot, so
  their rows were dropped. They are listed in `import.json` under "row for a guild Timothy
  is no longer in".
- Nothing about the old Mongo instance is needed again. Keep the dump.
