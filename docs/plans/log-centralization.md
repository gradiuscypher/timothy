# Log centralization: VictoriaLogs

Replaces the storage half of ADR 0014. Not yet built; this is the plan and the decisions
already taken, written down so the work can start from a fresh session.

## Why

ADR 0014 put every service's logs in one bind-mounted directory and bounded each file
itself. The durability and single-directory parts of that worked. Bounding the files did
not age well: nginx rotates nothing on its own, so it acquired `logrotate`, a config, an
entrypoint script backgrounding an unsupervised loop, two file modes that disable
rotation *silently* (one of which bit us during the build), and a `chmod 0777` init
container to make a shared bind mount writable by four different container users.

That is four pieces of machinery keeping two text files from growing. Moving to a log
store deletes all of it — retention becomes a flag on a database designed for it — and
adds querying we do not currently have. The point of this change is deletion, not
addition. If the diff does not read that way, something has gone wrong.

## Shape

```
backend ─┐
bot     ─┤   stdout (JSON, redacted in-process)
web     ─┤        │
cloudflared ─┘    ▼
             docker `local` driver      bounded buffer; `docker compose logs` still works
                  │
             Vector                     reads the Docker socket
                  │
             VictoriaLogs               named volume, 1 year retention
                  │
             127.0.0.1:9428  ──ssh -L──▶  vmui
```

Two new services.

## Decisions taken

### Redaction does not move

`timothy_logs` keeps its `Redactor`, `JsonFormatter`, secret registration and excepthooks.
It loses only the file handler. Scrubbing stays in-process, before anything leaves — and
matters more after this change, not less: an unredacted line now lands in a queryable,
indexed store rather than a flat file.

This is the half of ADR 0014 that survives intact, and ADR 0015 must say so explicitly
rather than leaving it implied.

### The Docker socket is mounted, and that is a real grant

Vector's `docker_logs` source reads `/var/run/docker.sock`. **This is root-equivalent on
the host.** `:ro` prevents writing to the socket *file* and constrains nothing about the
API reachable through it — a process holding this socket can create a privileged
container, mount the host filesystem, and read every secret on the machine.

Accepted deliberately. The trust boundary is already "whoever can run `docker` on this
host", and the alternatives cost more than they return here: `docker-socket-proxy` is a
third container to run and keep current, and tailing `/var/lib/docker/containers/`
read-only instead would force the `json-file` driver, because the `local` driver's format
is not tailable.

What we do instead of a proxy, none of which constrains the socket itself but all of which
shrinks everything around it:

- **Pin Vector to an image digest**, not just a tag. This is the one container where a
  supply-chain substitution is host compromise. The rest of the stack pins tags; this one
  goes further.
- **No ports, no ingress.** Vector listens on nothing. Its only inputs are the socket and
  the log content itself, so the realistic attack is malicious log content hitting a
  parser bug — low, but it is the surface that exists.
- `security_opt: [no-new-privileges:true]`, `cap_drop: [ALL]`, and a read-only root
  filesystem with a writable volume for checkpoints and disk buffers.
- **Run as non-root in the container if the image supports it.** The socket is
  `root:docker` mode 0660 on the host, so a non-root container process needs the host's
  `docker` gid: `group_add: ["${DOCKER_GID}"]`, found with `getent group docker | cut -d: -f3`.
  Verify what user the pinned Vector image runs as before assuming this works — and note
  that this is exactly the class of silent, mode-shaped failure that broke logrotate, so
  it wants an explicit check rather than an assumption.

Reversible: if this ever feels wrong, `docker-socket-proxy` drops in later by pointing
Vector's `docker_host` at it. Nothing else changes.

### Retention is one year

`-retentionPeriod=1y`. This is a side project and the point is being able to debug
something from eight months ago.

Two consequences to plan for:

- **A second, size-based bound is now load-bearing.** A year is long enough that a
  runaway producer — the client-error flood the `/client-logs` budget already guards
  against, or `TIMOTHY_LOG_LEVEL=debug` left on through a sweep — could fill the disk. Set
  a disk cap as well (~10GB). Note the semantics: when the cap is reached VictoriaLogs
  drops the *oldest* data, so effective retention silently shortens. That is the right
  failure mode, but it fails quietly, so it is worth looking at occasionally.
- **The health check is going to dominate the store.** The compose healthcheck hits
  `/health` every 10s, which is 8,640 uvicorn access lines a day and over three million a
  year — comfortably the largest single source, and all of it noise. Drop it at the
  collector (a Vector filter on `logger == "uvicorn.access"` and a `/health` path). This
  is what makes a year both cheap and readable. Reversible in one config line if it ever
  matters.

With that filter, expect single-digit MB/day raw and well under a gigabyte stored for the
year. Without it, the store is mostly a record of the container being alive.

**Privacy note, deliberately accepted.** A year of logs holds Discord user IDs, guild IDs
and — through `client_logs` and enforcement messages — ban reasons. Timothy now has two
overlapping records of who did what: `audit_log`, permanent by design and gated by
`TIMOTHY_OWNER_IDS` (ADR 0011), and these logs, gated only by shell access on the host.
That is a conscious state rather than an accident, and ADR 0015 should say so.

### The UI is loopback-only

`ports: ["127.0.0.1:9428:9428"]`, reached with `ssh -L 9428:localhost:9428 you@host`.

VictoriaLogs has no authentication of its own — it expects to sit behind something. **The
loopback bind is the authentication**: anyone with a shell on the host reads every log.
Consistent with the socket decision above, and it should be stated rather than assumed.

Binding explicitly to `127.0.0.1` also avoids the well-known case where Docker's port
publishing writes its own iptables rules and bypasses a host firewall — a bare
`9428:9428` would expose this to the network regardless of UFW.

This breaks the "nothing publishes a port" invariant at the top of `compose.yaml`. Amend
that comment; do not leave it quietly contradicted.

### stdout becomes JSON

The collector needs fields, not sentences. Add `TIMOTHY_LOG_FORMAT` (`console` | `json`),
defaulting to `console` so a bare `timothy-api` outside compose stays readable, with
compose setting `json` explicitly — matching how `compose.yaml` already spells out every
setting rather than leaning on a default.

Cost: `docker compose logs backend` becomes JSON. Worked around with `| jq -r .message`,
and largely obsolete once vmui is the place logs get read.

Payoff from work already done: the existing `JsonFormatter` emits `ts`, `message` and
`service`, which map straight onto VictoriaLogs' `_time_field`, `_msg_field` and
`_stream_fields` insert parameters. No reshaping needed for the Python services.

### Stream fields are `service` and `container_name` only

`guild_id`, `actor`, `client_url` and the rest stay ordinary fields — searchable, not
stream-indexed. Keeps stream cardinality flat, and doubles as a privacy property: nothing
per-user becomes an index dimension.

nginx and cloudflared emit plain text rather than JSON, so Vector routes them separately —
whole line into `_msg`, service derived from the container name.

## Phase 1 — add, alongside

Nothing is removed. Both paths run, so this is fully reversible and the safety net stays
up while the replacement is evaluated.

- `compose.yaml`: add `victorialogs` and `collector` services. Add a `x-logging:` YAML
  anchor (`driver: local`, `max-size: 10m`, `max-file: 3`) merged into all five existing
  services, so the buffer is bounded without repeating the block.
- `vector/vector.yaml`: `docker_logs` source; route JSON (backend, bot) from plain text
  (web, cloudflared); drop `/health` access lines; `http` sink to VictoriaLogs'
  `/insert/jsonline` with the field parameters above; disk buffer enabled.
- `.env.example`: `DOCKER_GID`, and a note on finding it.
- Verify: ingestion from all four services, the `/health` filter, the redaction assertion
  below, and the collector-restart gap under "Risks".

Run it for a week before phase 2.

## Phase 2 — delete the file layer

One commit, after phase 1 has proven itself.

- `compose.yaml`: remove the `logs` init service, four `./logs:/logs` mounts,
  `TIMOTHY_LOG_DIR`, `TIMOTHY_LOG_ROTATE_INTERVAL`, and cloudflared's
  `TUNNEL_LOGDIRECTORY`.
- `packages/logs/src/timothy_logs/__init__.py`: drop `_file_handler`,
  `RotatingFileHandler`, `MAX_BYTES`, `BACKUP_COUNT`, and the unwritable-directory
  fallback. Add the `TIMOTHY_LOG_FORMAT` branch.
- `apps/api/src/timothy_api/settings.py` and `apps/bot/src/timothy_bot/settings.py`:
  drop `log_dir` and the `LogDir` / `_optional_path` validator; add `log_format`.
- `web/Dockerfile`: drop `apk add logrotate`, both `COPY` lines, both `chmod` lines.
- Delete `web/logrotate.conf` and `web/docker-entrypoint.d/40-rotate-logs.sh`.
- `web/nginx.conf`: drop the two file `access_log`/`error_log` lines, keep the
  stdout/stderr pair and `log_format timothy` — dropping the query string still matters,
  now more than before.
- `.gitignore`, `.dockerignore`: drop `/logs/`.

## Phase 3 — docs

- **ADR 0015**, superseding ADR 0014's storage decision and explicitly carrying forward
  its redaction reasoning. Supersede rather than rewrite: 0014 records why files were
  tried, and that is worth keeping.
- `README.md` "Logs": replace the `grep`/`jq` recipes with LogsQL and the `ssh -L`
  command. State the two access facts — loopback is the auth, and the socket mount is a
  host-root grant.
- `compose.yaml` header comment: amend the "nothing publishes a port" claim.
- `.env.example`: retention and `DOCKER_GID`.

## Tests

- `packages/logs/tests`: drop the file-handler cases; keep and extend the redaction ones;
  assert stdout is one JSON object per line under `json`, and a sentence under `console`.
- **Extend the existing CI `stack` job**: bring the stack up, drive a request carrying a
  known fake token, query VictoriaLogs' API, and assert the line arrived *and* the token
  did not. That turns ADR 0014's central promise into an end-to-end assertion instead of a
  formatter-level one — and it is the class of test that would have caught the logrotate
  `chmod` bug.

## Risks

- **Collector restart gap.** Vector's `docker_logs` checkpointing is weaker than file
  tailing; a restart may skip lines. Bounded by restart time, with the Docker buffer as
  backstop. Measure it in phase 1 rather than assuming.
- **Store unavailable.** Vector's disk buffer absorbs it and catches up. Bounded, not
  lossless.
- **Nothing supervises correctness.** Rotation used to fail silently; ingestion can too.
  The CI stack assertion above is the guard.
- **Backups.** The SQLite database is the thing that matters and is unchanged. A year of
  logs in a named volume is nice to have, not something to build a backup story around.

## Verify before writing code

Against the *pinned* VictoriaLogs and Vector versions, not from memory:

- exact flag names and accepted formats for `-retentionPeriod` and the disk-usage cap
- the vmui path under `/select/`
- the `/insert/jsonline` query parameters for `_time_field`, `_msg_field`, `_stream_fields`
- current LogsQL syntax, for the README examples
- which user the Vector image runs as, and whether `group_add` is needed or redundant

Port 9428 and the `_msg` / `_time` / `_stream` field model are settled; the rest is not.
