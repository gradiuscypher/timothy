# A browser session carries a Discord-derived guild snapshot

Reading pools requires membership of some guild Timothy is in (ADR 0001). There is no
such thing as a membership *list* on Timothy's side, so the check is a question asked of
Discord once per guild until one says yes — and Discord paces those at about two a second.

Phase 5 measured the consequence on the real deployment: **51.9 seconds** for a full scan
of 123 guilds. The bot's fix was `X-Timothy-From-Guild`, which reorders the scan so the
guild the command came from is asked first. That took the everyday case to 1.31 seconds
and one call, and left one case untouched: a genuine non-member still pays a call per
guild before being refused. Phase 5's handoff carried it forward as unfinished.

The web UI cannot use the bot's fix at all. There is no interaction, so there is no guild
to name, and every browser caller would pay the full scan.

## Decision

The OAuth login asks for the `guilds` scope alongside `identify`, and the list Discord
returns is stored on the session row. For a caller holding a session, the membership scan
is **the intersection of that list with the guilds Timothy is in, and nothing else**:

- empty intersection — refused, with no Discord call at all;
- non-empty — each of those guilds is asked about, in the ordinary way, and the answer is
  whatever Discord says now.

The same snapshot narrows `GET /guilds`, which would otherwise resolve a permission for
every guild Timothy is in to find the handful the caller administers.

## Why this is not a stored grant

The distinction ADR 0001 draws is between authority *derived from Discord* and authority
*stored by Timothy*. This is the former, with a timestamp on it:

- The list came from Discord, for this user, with their consent, at a known moment.
- It is only ever used to decide **which guilds to ask about**. Membership itself is
  still resolved by `fetch_member` against Discord at request time, so somebody who has
  left a guild since logging in is refused despite their snapshot still naming it.
- In the other direction it can only ever *narrow*. A snapshot naming a guild the person
  is not in costs one wasted call and changes no decision, exactly like the bot's header.

The one thing it does decide on its own is the empty case, and it decides it the safe
way: refusing. A stale snapshot cannot admit anybody.

## Considered options

- **Fetch the user's guilds from Discord on every request.** Correct and current, and it
  needs the user's OAuth token kept and refreshed. Storing per-user Discord tokens is a
  much larger thing to get wrong than storing a list of guild IDs, and the token would be
  a credential Timothy has no other use for — it never acts as the user.
- **Trust the snapshot outright and skip `fetch_member`.** One fewer call, and it turns a
  login into up-to-a-week of standing access to whatever those guilds could read. Refused:
  the snapshot would then be the answer rather than the question.
- **Keep the full scan for browsers too.** Every page load behind up to 52 seconds of
  Discord calls, for the one screen a member with no administrator anywhere can reach.
  This is the same failure phase 5 fixed for the bot, and it is worse in a browser because
  nothing times out to tell the person what happened.

## Consequences

- `sessions` gains `guild_ids`, `username` and `avatar` (revision `0003`). The first is
  load-bearing; the other two are there so drawing "signed in as ..." costs no Discord
  call.
- **A guild Timothy joins after somebody logs in is invisible to them until they log in
  again.** Up to the session lifetime, a week by default. The failure is a refusal, and
  the fix is logging out and back in — but it is a real edge and it is the price of not
  holding a refresh token.
- The permission cache can no longer key membership on the user alone. "No" from a
  narrowed scan is only "no" about the guilds that scan covered, so the key is the user
  *and the set of guilds asked about* — ordering excluded, so the bot's reordering hint
  still shares one entry. Without this, a browser's narrow miss would answer the bot's
  wide question for a whole TTL.
- The `guilds` scope shows on Discord's consent screen. `guilds.members.read` would answer
  the question more directly and is deliberately not asked for: it reads roles and
  nicknames in every guild, and Timothy needs none of that.
