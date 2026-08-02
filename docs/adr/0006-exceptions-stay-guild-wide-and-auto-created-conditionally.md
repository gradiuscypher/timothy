# Exceptions stay guild-wide, and are auto-created only when they'd be undone

An exception excuses a user from every pool a guild subscribes to, not just the one that
listed them. We kept this, because it matches how moderators think — "I vouch for this
person in my server" — even though it means an exception granted over a minor pool also
silently excuses a later listing in a more serious one.

The old bot created an exception on *every* unban. That hook exists because the sweep would
otherwise re-ban the user within the hour, so a manual unban had to be made sticky. But it
fired for unrelated local bans too, filling the exception list with users who were never in
a pool and attributing them to user `"0"`.

Now the hook fires only when the unbanned user is actually listed in a pool the guild
subscribes to — the only case where the unban would otherwise be undone. Otherwise it is a
no-op.

## Consequences

- The exception list stays meaningful and reviewable.
- Timothy needs a first-class "system" actor for the exceptions it creates itself, rather
  than the magic user ID `"0"`.
- Exceptions granted for one pool still cover all pools. If that hole ever bites, the fix
  is an optional `pool_name` column where NULL preserves today's meaning, so existing rows
  migrate untouched.
