# The operations view belongs to a configured owner

Phase 6 added `/ops`: the queue, every guild's enforcement failures in one list, the
settings the deployment is actually running with, and how far through a sweep it is. It
was first gated on `ADMINISTRATOR` in the management guild, on the reasoning that this is
the closest thing ADR 0001's derived model has to "the operator".

That is the wrong reading of who those people are. Administering the management guild
makes somebody responsible for the **pools** — which users belong on which list, and why.
It does not make them responsible for the **deployment**. Those are different jobs and
they are frequently different people: a guild can have several administrators, and
Timothy's own internals are not what any of them signed up for.

## Decision

`TIMOTHY_OWNER_IDS` names whoever runs this deployment. It is the sole gate on
`Operation.READ_OPS`, which is the sole gate on every `/ops/*` route.

```
TIMOTHY_OWNER_IDS=242024455190577152
```

Usually one ID. Comma-separated if a deployment genuinely has more than one operator.

**Unset closes the operations view for everybody**, including the management guild's
administrators. It does not fall back to them — a fallback would silently re-merge the two
jobs this exists to separate, and the failure mode would be invisible, because the page
would simply keep working for the wrong people.

## Is this not exactly what ADR 0001 refused?

No, and the distinction is worth being precise about, because it would be easy to use this
as a precedent for something ADR 0001 really does rule out.

ADR 0001 rejected **full in-app RBAC**: per-pool owners and moderators, explicit grants,
stored per user, with Discord used only to bootstrap. That is a system of records about
who may do what, and it has to be administered, audited and kept in step with reality.

This is a **configuration value naming one person**, and it sits beside
`TIMOTHY_MANAGEMENT_GUILD_ID`, which is *already* a configured value that decides who owns
pools. Neither is a grant stored about a user; both are properties of the deployment, set
by whoever deploys it, in the same file as the bot token. If naming the guild is
consistent with ADR 0001 — and it always has been — then naming the operator is the same
kind of statement.

Two further properties keep it narrow:

- **It only ever restricts.** It gates one read-only surface, and it gates it *more*
  tightly than the derived rule it replaced. Nothing becomes reachable that was not
  reachable before.
- **It grants nothing else.** An owner who is not an administrator anywhere still cannot
  create a pool, list a user, subscribe a guild or read the audit log. `READ_OPS` is the
  only operation this requirement appears against, and the table in
  `timothy_api.policy` is where you can see that in one screen.

## Consequences

- `Requirement.OWNER` is the first requirement resolved without asking Discord anything,
  which makes it the cheapest check in the system. It is also the first that could be
  wrong without Discord being wrong: if the ID is mistyped, nobody can see the operations
  view, and the symptom is a 403 rather than an error message. `.env.example` says so.
- **A malformed entry is dropped rather than fatal.** Because this setting only narrows, a
  typo produces a smaller set of owners and never a larger one, so failing closed on the
  bad entry is safer than refusing to start the backend.
- The owner is identified by Discord user ID, so the browser path still depends on the
  OAuth login being configured (ADR 0010) — the session is what proves the caller is that
  user. A service caller can also present the owner's ID in `X-Timothy-Actor`, which is no
  weaker than the rest of the API: that assertion is already only as good as the internal
  token (ADR 0008).
- `/auth/me` reports `is_owner` so the UI can draw the navigation. Like `manages_pools`
  beside it, that is a hint and not a gate — the `/ops` routes resolve it again for
  themselves, so a stale `true` produces a 403 rather than an escalation.
- If the operations view ever grows a *write* — resuming a paused guild, flipping dry run
  — that is a different decision and wants its own ADR. Everything under `/ops` today is
  read-only, and that is what makes a single configured ID a proportionate gate.
