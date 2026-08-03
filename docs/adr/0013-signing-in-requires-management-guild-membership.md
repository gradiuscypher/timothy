# Signing in requires management guild membership

Until now the web UI's front door was Discord's consent screen and nothing else. Anybody
with a Discord account could complete the OAuth flow and hold a session. That was
defensible on the reasoning ADR 0001 established — a session says who you are, and every
permission is resolved against Discord afterwards, so a session on its own is worth
nothing — and it is still true as far as it goes.

What it leaves is a login that succeeds for everybody on Discord. A stranger reaching the
tunnel hostname gets signed in, is shown a page with no navigation on it, and holds a row
in `sessions` for a week. Nothing escalates, but the session table becomes a log of
whoever found the URL, and the surface behind the cookie is the whole API rather than the
one screen they can actually use. The place to say no is the door.

## Decision

`/auth/callback` issues a session only if Discord's answer to `/users/@me/guilds` contains
`TIMOTHY_MANAGEMENT_GUILD_ID`. A login from anybody else ends there: no session row, no
cookie, and a redirect to `/?login=denied`, which the SPA renders as a message saying
membership of the management server is what is missing.

**Membership, and nothing more.** No role, no `ADMINISTRATOR`, no entry in
`TIMOTHY_POOL_MANAGER_ROLE_IDS`. Being in the server is the whole test, and it is a door
rather than a grant: everybody it admits still resolves every permission against Discord
on every request, exactly as before.

The check costs no extra Discord call. The `guilds` scope is already requested for the
snapshot ADR 0010 stores, and the guild list already arrives with the identity.

**An unset `TIMOTHY_MANAGEMENT_GUILD_ID` closes login**, and says so at `/auth/login` with
a 503 naming the setting, alongside the OAuth credentials it already named. Nobody is in
guild zero, so the alternative is a login that refuses everybody after a round trip to
Discord — a missing setting wearing a wrong password's symptom.

## Why the door and not the requests behind it

Membership could instead be resolved per request, the way authority is. It is deliberately
not:

- It is not authority. Nothing is decided by it except whether a browser gets a session at
  all, and every permission behind it is still resolved against Discord at request time
  (ADR 0001). Making it a per-request check would add a Discord call to every request to
  narrow a set that is already narrowed by the permission that request actually needs.
- The consequence is bounded and small. Somebody who leaves the management server keeps
  their session until it expires — a week by default — and what that session lets them do
  is what Discord says they may do *now*, which for a departed member is the guilds they
  are still in and the roles they still hold. Revoking it is `DELETE FROM sessions WHERE
  user_id = ...`, or waiting.

This is the same trade ADR 0010 made for the guild snapshot, for the same reason: what
Discord said at login narrows the question, and Discord at request time answers it.

## Considered options

- **Leave login open to everybody.** What was there. Safe in the sense that mattered and
  wrong in the sense that a stranger completing a login and being issued a credential is
  not a thing to shrug at, even a credential that opens nothing.
- **Require a role in the management guild.** That is `TIMOTHY_POOL_MANAGER_ROLE_IDS`,
  and it already gates what pool managers do. Using it for the door would mean nobody
  else — a guild administrator signing in to check their own server's subscription, an
  owner reading `/ops` — could log in at all. The door and the desk are different checks.
- **An explicit allow-list of user IDs.** A second list to maintain, in a file, which
  drifts from the server it is meant to describe. Discord already holds the membership,
  and ADR 0001 is about deriving from it rather than copying it.

## Consequences

- **Whoever runs the deployment has to be in the management server.** `TIMOTHY_OWNER_IDS`
  is still what grants `/ops` (ADR 0011) and is still resolved without asking Discord —
  but the owner reaches it through a browser session, and the session now needs
  membership. An owner outside the server can use the API with the internal token and
  cannot use the web UI. Being *in* the server is not the same as administering it, so
  ADR 0011's separation is untouched.
- **A browser session's snapshot now always names the management guild.** The empty
  intersection in `deps._scan_set` — refusing a caller with no Discord calls at all — is
  therefore unreachable from a login, and remains as the guard for the case that is left:
  Timothy being removed from the management guild while sessions are live. The tests
  reach it that way.
- The refusal is a redirect, not a 403, because `/auth/callback` is somewhere a browser
  lands. `?login=denied` and `?login=failed` are separate because the advice differs:
  one is worth retrying, the other is not.
- It is logged at info with the user ID, so an operator can see who tried. Discord already
  identified them by the time the check runs, and that is the moment worth recording.
