#!/usr/bin/env bash
# Run each log recipe through `make`, with `docker` stubbed to print a fixture.
#
# What this catches is not jq logic — it is the layer under it: a filter split across two
# arguments, a `$` make ate, a quote a line continuation broke. All three are invisible
# when the pipeline is pasted into a shell by hand, and fatal when `make` assembles it.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
stub="$(mktemp -d)"
trap 'rm -rf "$stub"' EXIT

cat > "$stub/fixture.jsonl" <<'JSON'
{"ts":"2026-08-07T13:37:25+0000","level":"INFO","service":"api","logger":"timothy_api.enforcement.worker","message":"job 41 (backfill_user_names) started","extra":{"job_id":41,"job_kind":"backfill_user_names","job_payload":{}}}
{"ts":"2026-08-07T13:41:02+0000","level":"INFO","service":"api","logger":"timothy_api.enforcement.handlers","message":"user name backfill: 487 name(s) from 500 lookup(s)"}
{"ts":"2026-08-07T13:41:02+0000","level":"INFO","service":"api","logger":"timothy_api.enforcement.worker","message":"job 41 (backfill_user_names) finished in 217.4s","extra":{"job_id":41,"job_kind":"backfill_user_names","seconds":217.4}}
{"ts":"2026-08-07T13:42:00+0000","level":"ERROR","service":"api","logger":"timothy_api.enforcement.worker","message":"job 42 (enforce_guild) failed","exception":"Traceback (most recent call last):\n  KeyError: 'guild_id'","extra":{"job_id":42,"job_kind":"enforce_guild"}}
{"ts":"2026-08-07T13:42:01+0000","level":"DEBUG","service":"api","logger":"timothy_api.enforcement.engine","message":"guild 17 user 23: skip_user_absent","extra":{"guild_id":17,"user_id":23,"decision":"skip_user_absent"}}
this line is not JSON and must be skipped rather than abort the pipe
JSON

# `docker compose logs …` prints the fixture and ignores every flag, including -f: a
# recipe that follows would otherwise never return.
cat > "$stub/docker" <<'STUB'
#!/usr/bin/env bash
cat "$(dirname "$0")/fixture.jsonl"
STUB
chmod +x "$stub/docker"

fail=0
expect() {
  local target="$1" wanted="$2" output
  output="$(cd "$root" && PATH="$stub:$PATH" make --no-print-directory "$target" 2>&1)" || {
    printf '  %-10s FAILED to run:\n%s\n' "$target" "$output"
    fail=1
    return
  }
  if grep -qF -- "$wanted" <<<"$output"; then
    printf '  %-10s ok\n' "$target"
  else
    printf '  %-10s did not print %q. Got:\n%s\n' "$target" "$wanted" "$output"
    fail=1
  fi
}

echo "log recipes:"
expect logs     "2026-08-07T13:42:00+0000 ERROR job 42 (enforce_guild) failed"
expect errors   "KeyError: 'guild_id'"
expect jobs-log "job 41 backfill_user_names finished in 217.4s"
expect jobs-now "job 42 (enforce_guild) failed"
expect names-log "user name backfill: 487 name(s) from 500 lookup(s)"
expect pairs    "guild 17 user 23 skip_user_absent"

# The fixture's last line is not JSON. Any recipe that aborted on it would have printed
# nothing after it, and every assertion above would still have passed — so check directly
# that jq skipped it rather than dying.
output="$(cd "$root" && PATH="$stub:$PATH" make --no-print-directory logs 2>&1)"
if grep -q "not JSON" <<<"$output"; then
  echo "  logs       leaked a non-JSON line into the output"
  fail=1
elif grep -qi "parse error" <<<"$output"; then
  echo "  logs       aborted on a non-JSON line"
  fail=1
else
  echo "  non-JSON  skipped cleanly"
fi

exit "$fail"
