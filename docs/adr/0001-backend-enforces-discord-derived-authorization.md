# Backend enforces authorization, derived from Discord permissions

The original bot had no authorization code at all — it relied entirely on Discord's
`default_member_permissions: 0` and on registering management commands only in the
management guild. That model can't extend to a web UI and leaves the API unprotected.

In the rewrite the backend API is the single enforcement point for every action. Both
the bot and the web UI are thin clients that call it. Authority is still *derived* from
Discord rather than stored in a bespoke RBAC system: guild-scoped operations require the
acting user to hold `ADMINISTRATOR` in the target guild, and pool-scoped operations
require `ADMINISTRATOR` in the designated management guild.

## Considered Options

- **Port as-is** — keep trusting Discord's command scoping, and check permissions only in
  the web UI. Rejected: two divergent enforcement paths, and the API is open to anything
  that can reach it.
- **Full in-app RBAC** — per-pool owners and moderators, explicit grants, Discord used only
  to bootstrap. Rejected for now as more machinery than the current usage justifies. The
  derived model can be replaced by this later without changing the API surface, since
  callers already go through the backend.

## Consequences

There is exactly one management guild, named by configuration.

Because a future relaxation is already anticipated, permission decisions live in one policy
module rather than being inlined at each endpoint. The known case is looking up why a user
is listed: today that requires the management guild, but the intent is to let a subscribing
guild's own moderators do it. That should be a change to one policy rule, not a hunt
through handlers.
