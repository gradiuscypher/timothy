# Dry run records intentions, not attributions

CONTEXT.md defines dry run as the mode that "records every enforcement it would perform
but issues nothing to Discord", and PLAN.md's phase 5 rehearses the whole cutover in it,
diffing Timothy's intended actions against the old bot's behaviour. So dry run has to
write *something*. The question is where.

Not `enforcement_outcomes`. That table is not a log of what Timothy considered — it is the
attribution that makes a ban revertable (ADR 0005), and the only evidence Timothy has that
a ban is its own. A `banned` row written for a ban that was never issued is a false
attribution, and the moment dry run came off, a revert would act on it and unban a user
Timothy never touched: exactly the thing ADR 0005 exists to prevent, arrived at from the
opposite direction.

Dry run therefore writes an `enforcement.dry_run` line to the audit log — which already
covers Timothy's own actions — and writes no outcomes at all.

## Consequences

- Phase 5 diffs the audit log, not the outcomes table.
- Dry run does not dedupe. With no `banned` or `warned` row to settle a user, every sweep
  restates the same intention. For a diff that is right; it is also why these could never
  have been outcome rows.
- A revert is frozen too, in the same way and for the same reason: it is an action, and
  dry run issues none. Its audit line says `would: revert`.
- The circuit breaker still halts a simulated run, and still says so, but does **not**
  persist the per-guild pause. A rehearsal against production data must not leave real
  guilds paused when dry run comes off. The audit row carries `dry_run: true` so the two
  cases are distinguishable afterwards.
