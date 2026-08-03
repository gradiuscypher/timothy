# Logs go to a queryable store, not to files

Supersedes the storage half of [ADR 0014](0014-logs-are-redacted-files-in-one-shared-directory.md).
Its redaction half is carried forward intact and is restated below, because it matters
more after this change rather than less.

ADR 0014 put every service's logs in one bind-mounted directory and bounded each file
itself. Durability and one-place-to-look both worked and are kept. Bounding the files did
not age well.

nginx rotates nothing on its own, so it acquired `logrotate`, a config file, an entrypoint
script backgrounding an unsupervised loop, two file modes that disable rotation *silently*
— one of which bit us during the build — and a `chmod 0777` init container to make a
shared bind mount writable by four different container users. That is four moving pieces
whose entire job is keeping two text files from growing, and one of them was already
documented in 0014 as failing invisibly if it ever stopped.

A log store does that with a flag. The point of this change is deletion; querying is the
thing we get for free on the way past.

## Decision

**VictoriaLogs holds the logs, in a named volume, for a year.** `-retentionPeriod=1y`,
because this is a side project and the whole value of a log is answering "when did this
start?" about something from eight months ago.

**Vector collects them from the Docker socket.** Every service writes JSON to stdout and
stops there — the twelve-factor answer that 0014 rejected only because nothing was
collecting stdout. Now something is. The daemon's `local` driver keeps a bounded buffer
behind it, so `docker compose logs` still works for recent history.

**Redaction does not move.** `timothy_logs` keeps its `Redactor`, its formatters, its
secret registration and its excepthooks; it loses only the file handler. Scrubbing stays
in-process, before anything leaves the process that holds the credential. This is the half
of 0014 that survives unchanged, and it is load-bearing in a way it was not before: an
unredacted line now lands in an indexed, queryable store rather than in a flat file. The
argument for it is unchanged and still in 0014 — exact registered values catch what shape
patterns cannot, which is a token a library interpolated into a URL or an exception
message where nothing labels it as a credential.

**`TIMOTHY_LOG_FORMAT` chooses what stdout looks like.** `console` by default, so a bare
`timothy-api` outside compose stays readable by eye; compose sets `json` explicitly. The
collector needs fields, not sentences. The existing `JsonFormatter` already emitted `ts`,
`message` and `service`, which map onto VictoriaLogs' `_time_field`, `_msg_field` and
`_stream_fields` without reshaping — the format was designed for `jq` and turned out to
fit a log store.

**Stream fields are `service` and `container_name`, and nothing else.** `guild_id`,
`actor`, `client_url` and the rest stay ordinary fields: searchable, not stream-indexed.
That keeps stream cardinality flat, and doubles as a privacy property — nothing per-user
becomes an index dimension.

**The collector routes on what a line is, not on which service sent it.** Any line that
parses as a JSON object is unpacked into fields; anything else keeps the whole line as its
message. Deciding by service name would mean a lookup table that silently misroutes when a
service is renamed or added, and the first version of this config did exactly that — it
derived the service by stripping `timothy-` off the container name, which quietly
mislabelled every stream the moment it ran under a different compose project name. The
service name now comes from compose's own label. Both were caught by running it; neither
would have shown up in review.

## The Docker socket mount is a host-root grant

Vector's `docker_logs` source reads `/var/run/docker.sock`. **This is root-equivalent on
the host.** `:ro` prevents writing to the socket *file* and constrains nothing about the
API reachable through it: a process holding this socket can create a privileged container,
mount the host filesystem, and read every secret on the machine.

Accepted deliberately. The trust boundary here is already "whoever can run `docker` on
this host", which is the same argument 0014 used for `chmod 0777`. The alternatives cost
more than they return: `docker-socket-proxy` is a third container to run and keep current,
and tailing `/var/lib/docker/containers/` read-only instead would force the `json-file`
driver back, because the `local` driver's format is not tailable.

What we do instead — none of which constrains the socket, all of which shrinks everything
around it:

- **The Vector image is pinned by digest**, not only by tag. The rest of the stack pins
  tags; this is the one container where a supply-chain substitution is host compromise.
- **No ports, no ingress.** The collector listens on nothing. Its only inputs are the
  socket and log content itself, so the realistic attack is malicious log content reaching
  a parser bug — a small surface, but the one that exists.
- `no-new-privileges`, `cap_drop: [ALL]`, and a read-only root filesystem with a writable
  volume for checkpoints and the disk buffer.
- **It runs as root, and that was checked rather than assumed.** The pinned image sets no
  `USER`, ships no unprivileged account, and owns `/var/lib/vector` as root, so running it
  as a non-root user would need an init container to chown the buffer volume — which is
  precisely the machinery this ADR exists to delete. The plan for this change assumed
  `group_add` for the host's `docker` gid would be enough; it is not, and the difference
  is the kind of silent, mode-shaped failure that broke logrotate.

Reversible: `docker-socket-proxy` drops in later by pointing `docker_host` at it, and
nothing else changes.

## The loopback bind is the authentication

`ports: ["127.0.0.1:9428:9428"]`, reached with `ssh -L 9428:localhost:9428 you@host`.

VictoriaLogs has no authentication of its own — it expects to sit behind something. Here
that something is SSH. **Anyone with a shell on this host reads every log.** Consistent
with the socket decision above, and stated rather than left implied.

The explicit `127.0.0.1:` is load-bearing. A bare `9428:9428` would have Docker write its
own iptables rules and publish the store to the network regardless of what UFW says.

This breaks the "nothing publishes a port" invariant `compose.yaml` opened with, and that
comment is amended rather than left quietly contradicted.

## A year of logs is a second record of who did what

Deliberately accepted, and worth writing down. A year of logs holds Discord user IDs,
guild IDs, ban reasons — through `client_logs` and enforcement messages — and **client IP
addresses**. Timothy now keeps two overlapping records of who did what: `audit_log`,
permanent by design and gated by `TIMOTHY_OWNER_IDS` (ADR 0011), and these logs, gated
only by shell access on the host. That is a conscious state rather than an accident.

The IP addresses are new with this ADR and are the sharpest of these. nginx used to log
cloudflared's container address — the same 172.x on every line, useless to a reader and
personal data about nobody. `realip` now recovers the true client address from
`CF-Connecting-IP`, which makes the access log worth reading and simultaneously turns it
into a year-long record of who connected from where, correlatable with the Discord IDs on
neighbouring lines. Worth it: "was this one person or thirty" is a question the log could
not previously answer at all. But it is a real change in what the store holds, not a
formatting improvement, and shortening `TIMOTHY_LOG_RETENTION` is the lever if that trade
ever stops being the right one.

## Consequences

- **Healthcheck access lines are dropped at the collector.** The compose healthcheck hits
  `/health` every 10s: 8,640 uvicorn access lines a day, over three million a year, and
  comfortably the largest single source in the store — all of it noise. A filter on
  `logger == "uvicorn.access"` and the `/health` path is what makes a year both cheap and
  readable. Without it the store is mostly a record of the container being alive. With it,
  expect single-digit MB/day and well under a gigabyte for the year. Reversible in one
  line if it ever matters.
- **A size cap is now load-bearing, and it fails quietly.**
  `-retention.maxDiskSpaceUsageBytes=10GB`. A year is long enough that a runaway producer
  — the client-error flood the `/client-logs` budget already guards against, or
  `TIMOTHY_LOG_LEVEL=debug` left on through a sweep — could fill the disk. At the cap
  VictoriaLogs drops the **oldest** day partitions, so effective retention silently
  shortens rather than ingestion failing. That is the right failure mode and it is a
  silent one: a query from ten months ago coming back empty is the symptom.
- **A collector restart loses a bounded handful of lines.** `docker_logs` checkpointing is
  weaker than file tailing. Measured rather than assumed: a container emitting 20 lines a
  second across a `docker compose restart collector` lost 5 of 400, in one contiguous gap
  at the restart — roughly a quarter-second of output. Bounded by restart time.
- **The store being down is absorbed, not survived indefinitely.** Vector buffers to disk
  and catches up. Past 256MiB it drops the newest events rather than blocking, because a
  log store must not be able to take the stack down by applying backpressure to every
  container's stdout.
- **`docker compose logs backend` is now JSON.** `| jq -r .message` undoes it, and it is
  largely moot once vmui is where logs get read.
- **Nothing supervises correctness.** Rotation used to fail silently; ingestion can too,
  and the size cap above fails silently by design. The guard is an assertion in CI's
  `stack` job: drive a request carrying a known fake token, query the store, and assert
  the line arrived *and* the token did not. That turns 0014's central promise into an
  end-to-end check rather than a formatter-level one — and it is the class of test that
  would have caught the logrotate `chmod` bug, where every piece was correct and the
  assembly was not.
- **Backups are unchanged.** The SQLite database is the thing that matters. A year of logs
  in a named volume is nice to have, not something to build a backup story around.

## Status

The store and the collector are running. The file layer from ADR 0014 is still in place
alongside them and is removed in a follow-up commit, once this has proven itself — both
paths running means the safety net stays up while the replacement is evaluated, and means
this is reversible by deleting two services.
