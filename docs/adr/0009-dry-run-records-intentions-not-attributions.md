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
covers Timothy's own actions — and writes no *attribution*.

That last word was originally "and writes no outcomes at all", which was broader than the
argument above supports, and broader than the code turned out to be. The phase 5 rehearsal
found dry run writing `skipped_exception` rows and, going by the sentence rather than the
reasoning, it looked like a defect. It is not one, and the sentence was the thing that was
wrong.

`skipped_exception` is not an attribution. It says the guild vouched for this user and
Timothy therefore did nothing — a claim that is true whether or not dry run is on, and one
no revert can ever act on, because reverting keys strictly on a `banned` outcome. The row
also earns its place in a rehearsal: it is what stops the sweep asking Discord about an
excepted user every round, and at the couple of member lookups a second Discord allows per
guild, that is not a rounding error.

`banned` and `warned` stay unwritten. Those are the claims that would be false.

## Consequences

- Phase 5 diffs the audit log, not the outcomes table.
- Dry run does not dedupe *the intentions*. With no `banned` or `warned` row to settle a
  user, every sweep restates the same intention. For a diff that is right; it is also why
  these could never have been outcome rows. Excepted users are the exception, in both
  senses: their `skipped_exception` row is written, so they settle and drop out of the
  next round's candidates.
- A rehearsal therefore leaves `skipped_exception` rows behind in whatever database it
  ran against. They are correct rows — the exceptions they record are real — so this is
  worth knowing rather than worth preventing.
- A revert is frozen too, in the same way and for the same reason: it is an action, and
  dry run issues none. Its audit line says `would: revert`.
- The circuit breaker still halts a simulated run, and still says so, but does **not**
  persist the per-guild pause. A rehearsal against production data must not leave real
  guilds paused when dry run comes off. The audit row carries `dry_run: true` so the two
  cases are distinguishable afterwards.
