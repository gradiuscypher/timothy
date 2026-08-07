# Guild diagnostics come from the gateway's cache, not from REST

A guild that has misconfigured Timothy finds out slowly and badly. Bans fail, and the only
evidence is `failed` rows in `enforcement_outcomes` carrying whatever discord.py said —
usually `403 Forbidden`, which never names whose role was in the way. The one screen that
aggregates those, `/ops/failures`, belongs to whoever runs the deployment (ADR 0011), not
to the guild administrator who can fix it in thirty seconds.

Three questions have to be answerable on a guild's own configuration page: does Timothy
hold `BAN_MEMBERS` here at all, which roles sit at or above its own and are therefore
permanently out of reach, and why did *this* ban fail. Answering them needs three facts
the backend does not have — Timothy's resolved permissions in the guild, every role's
position, and **how many people hold each role**.

The third is the one that decides the design. Discord's API has no "members of this role"
count. Getting it over REST means paginating every member of every guild, a thousand at a
time, on every refresh — a recurring draw on the same rate limit that enforcement runs on,
for a number that is decoration next to the ban it explains.

The bot already has all three, for free. It runs with the privileged `members` intent so
that "banned at the door" works (ADR 0004), and discord.py chunks every guild at startup as
a consequence: the full member list, the role list with positions, and
`guild.me.guild_permissions` are sitting in its process memory.

**So the bot observes and the backend stores.** A loop reports every guild on a fifteen-
minute cadence, staggered across the interval; the backend writes it to `guild_diagnostics`
and `guild_roles`, replacing each guild's roles wholesale. This is what the bot already does
with guild names, and for the same stated reason: the gateway has it, and the backend has no
cheap way to ask for it later.

## Consequences

- **`DiscordPort` does not grow.** ADR 0007's six operations stand unamended. The one live
  question — which roles does this user hold *right now* — is the existing `fetch_member`;
  the positions to compare them against come from the stored snapshot. A seventh operation
  for role hierarchy would have had to be implemented in the adapter and honestly faked, to
  answer something the gateway was already holding.

- **ADR 0003 is not bent.** The backend remains the only thing that makes Discord calls.
  The bot makes none for this: it reads its own cache and posts the result over the same
  internal API it relays events on, under `Requirement.SYSTEM` for the same reason a
  gateway event is — there is no human behind it to derive authority from.

- **The refresh button is a request, not a call.** The backend cannot reach the bot, so
  `POST /guilds/{id}/diagnostics/refresh` records the guild in an in-memory queue and the
  bot collects it on a twenty-second poll. An administrator who has just moved a role sees
  the answer in well under a minute instead of waiting out the round. The queue drains on
  read and is not durable: a request lost to a restart costs one stale snapshot until the
  next round, and a queue that needed acknowledging would grow without bound whenever the
  bot was down.

- **Member counts are nullable, and that is load-bearing.** A guild whose members were
  never chunked would report `len(role.members)` as zero for every role — a confident claim
  that a blind spot nobody can measure is empty. So an unchunked guild reports `null` counts
  and a `member_counts_complete` of false, and the UI leaves the column blank.

- **The reported total is a ceiling, not a count.** Anyone holding two unbannable roles is
  counted twice. Deduplicating would mean shipping the member lists themselves to the
  backend, which is precisely the data this design keeps in the bot, so the figure is
  presented as "up to N".

- **A guild nobody has looked at answers 404, not a row of defaults.** There is no honest
  value for `can_ban` to take before anything has checked, and an all-clear nobody measured
  is worse than no answer. The UI says "not checked yet".

- **The explanation of a failure is live, and describes now.** Someone reading it is about
  to go and move a role; what they need is whether it would work today, not what was true
  when the ban failed last week. A failure whose cause has since been fixed says so. The
  cost is one member lookup per row somebody actually opens, which is why the list itself
  is a separate, database-only request.

- **Diagnostics are up to fifteen minutes stale** unless refreshed, and there is no attempt
  to make them live. Nothing enforces off them — they explain what enforcement already did.
