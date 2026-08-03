# Pool management is a role, not guild administration

ADR 0001 derived authority over pools from `ADMINISTRATOR` in the management guild: hold it
there, and you may create pools, list users on them, and read the audit log. That was the
cheapest correct thing to do at the time, and it was correct — the management guild existed
precisely to be the place where pool authority came from.

It is too coarse in practice, and the reason is what the two permissions actually mean.
`ADMINISTRATOR` in a Discord guild is a *server operations* permission: it lets somebody
rename channels, edit roles, configure the server. Managing pools decides **who gets banned
from every guild that subscribes**. The management guild will accumulate administrators for
ordinary server-running reasons — someone who set up the channels, someone who manages the
other bots — and every one of them silently becomes able to ban a user from a hundred and
twenty-three guilds.

The blast radii are not comparable, so the permissions should not be the same one.

## Decision

`TIMOTHY_POOL_MANAGER_ROLE_IDS` names the roles in the management guild whose holders own
pools and listings.

```
TIMOTHY_POOL_MANAGER_ROLE_IDS=1234567890123456789
```

Usually one role. Comma-separated if a deployment wants more than one, so that pool
management can be handed to two groups without merging them in Discord.

It replaces `Requirement.MANAGEMENT_ADMIN` entirely, against the three operations that had
it: `MANAGE_POOLS`, `MANAGE_LISTINGS` and `READ_AUDIT_LOG`. The audit log follows the other
two because it is largely the record *of* the other two — the people who can list a user
are the people who review the listing.

**Administering the management guild grants none of this.** There is no fallback. An
administrator who should manage pools gives themselves the role, which they always can —
and that act is then visible in Discord's own role list, rather than being an invisible
consequence of a permission they already held for unrelated reasons.

**Unset closes pool management for everybody.** Same reasoning as `OWNER_IDS` (ADR 0011): a
fallback to the administrators would make an unconfigured variable mean "open to whoever it
used to be open to", which is the one thing it must not mean. Deploying this the first time
therefore takes a step — create the role, assign it, set the variable — and that step is
the point.

## What this does not change

**Guild subscriptions stay with each guild's own administrators.** `MANAGE_SUBSCRIPTIONS`,
`MANAGE_EXCEPTIONS`, `MANAGE_NOTIFICATION_CHANNEL` and `READ_GUILD` are all still
`TARGET_GUILD_ADMIN`, and deliberately so. The two halves of the model are answering
different questions:

| | Pools | A guild's own settings |
| --- | --- | --- |
| Who | A role in the management guild | `ADMINISTRATOR` in that guild |
| Why | A listing bans people in every subscribing guild | A subscription binds only the guild that holds it |

Nobody should have to be granted anything by this deployment to decide what happens in
their own guild — that is theirs already, and Discord already says who they are. Requiring a
role for it would mean a guild joining Timothy had to wait on somebody else before it could
configure itself.

## Is this in-app RBAC, which ADR 0001 refused?

No, for the same reason ADR 0011 was not. What is stored here is a *configuration value*
naming a role, sitting beside `MANAGEMENT_GUILD_ID` in the same `.env`. The grants
themselves live in Discord, are made in Discord's UI, and are audited by Discord. Timothy
stores no record of who may do what and administers no such record.

The check is still derived, and still resolved live against Discord on every request. It
asks a different question of Discord than it used to — "do you hold this role" rather than
"do you hold this permission" — which is a change of rule, not a change of model.

## Consequences

- **The lookup changes shape.** Pool authority is resolved with `fetch_member` rather than
  `guild_permissions`, because roles are read off the member. `Member` therefore carries
  `role_ids`, and the adapter drops `@everyone` from it — `@everyone`'s ID is the guild's
  own, so carrying it would make a `POOL_MANAGER_ROLE_IDS` that named the management guild
  admit every member of it.
- **The cache holds the roles, not the answer.** `PermissionResolver` caches the set of
  roles a member holds and intersects it per request, so changing the setting takes effect
  on the next request rather than at the end of a TTL nobody can see.
- **`/auth/me` and the gate share one function.** `deps.manages_pools` answers both, so the
  navigation the SPA draws and the check the routes make cannot disagree about who manages
  pools — which is exactly how "unset means closed" would otherwise decay into "unset means
  open" on one of the two paths.
- **Existing deployments lose pool access until the variable is set.** That is a deliberate
  fail-closed migration, and the symptom is a 403 on pool routes with the Pools and Audit
  log links absent from the UI, not an error at startup.
- **The slash commands are gated twice, by different things.** Discord's own
  `default_permissions()` decides who *sees* `/add_ban` in the management guild, and a
  server administrator can already point that at the same role in Integrations. The
  backend's check is the one that matters; the Discord-side setting is there so the command
  does not appear to people it will refuse.
- **A member of the management guild with no roles is now indistinguishable from a
  non-member**, as far as pool authority goes. Both hold nothing, and neither may manage
  pools. That collapse is intended: there is no case where "is in the guild" should mean
  anything on its own.
