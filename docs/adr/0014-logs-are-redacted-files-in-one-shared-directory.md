# Logs are redacted files in one shared directory

Every service wrote to stdout and stopped there. That is the twelve-factor answer and it
is the right default, but it assumes something is collecting stdout. Nothing here was.
What actually held the logs was the Docker daemon's per-container JSON buffer, which is
discarded when the container is recreated — so a `docker compose up -d --build`, the
ordinary way anything is deployed here, deleted the record of everything that had gone
wrong before it.

The failures that matter most are the ones that take days to notice. A fan-out that
abandoned jobs at 3am, a guild the circuit breaker paused, a sweep that never finished:
none of those page anybody, all of them are found later by someone asking "when did this
start?" — and that question was unanswerable. On top of that, three of the four services
logged somewhere different, and the browser, where a React crash is the *only* record of
the fault, logged nowhere at all.

## Decision

**One directory, bind-mounted, holding every service.** `./logs` on the host is mounted
at `/logs` in the backend, the bot, nginx and cloudflared. It survives `docker compose
down`, an image rebuild, and the machine restarting, and it is readable with `grep`
rather than with `docker`.

```
logs/backend.log      JSON lines — the API, the worker, the sweeper, uvicorn
logs/bot.log          JSON lines — the gateway client and the relay
logs/web-access.log   nginx, one line per request, without query strings
logs/web-error.log    nginx
logs/cloudflared.log  the tunnel
```

**Python processes share `timothy_logs`.** One `configure()` call per entry point
installs a console handler (so `docker compose logs` is unchanged) and a rotating JSON
file handler, at 10MB × 10 per service. It also claims `sys.excepthook`,
`threading.excepthook` and `sys.unraisablehook`, because each of those is a way a process
can die with its log file ending one line before the reason.

nginx gets `logrotate` in its own image, driven by a loop the entrypoint backgrounds.
`nginx -s reopen` in `postrotate` is what makes it work: without the signal nginx holds
the renamed inode and keeps writing to `web-access.log.1` forever, so the rotation appears
to succeed and the live file never grows again.

`uvicorn.run(log_config=None)` is part of this and not incidental. Uvicorn's default
logging config installs its own handlers and turns off propagation, which would route the
access log and every ASGI traceback around the file handler.

**The formatter redacts, not a filter.** `RedactingFormatter` scrubs the final string.
A `logging.Filter` sees `msg` and `args` separately and never sees the traceback, and a
traceback is precisely where a credential surfaces — in the repr of the arguments to the
frame that raised. Two passes: the exact secret values the process was started with,
which each entry point passes in from its own settings, and then a set of shape-based
patterns for the credentials this process never held.

**nginx logs `$uri`, not `$request`.** `combined` logs the request line, and
`/api/auth/callback?code=...` carries a live OAuth authorization code. Dropping the query
string keeps the route — which is what anyone debugging wants — and means no credential
reaches that file to be redacted in the first place.

**The browser reports its own crashes** to `POST /api/client-logs`, behind the same
authentication as everything else, capped per actor per minute, and never above `error`.
A blank screen with a `TypeError` in a closed devtools console was the one class of fault
with no record anywhere on the host.

## Why not a log collector

Loki, Vector, or the daemon's own driver pointed at a file would each do this and more.
They are also a service to run, configure and keep up, in a deployment whose whole shape
is "four containers and a tunnel". The thing that was actually missing was durability and
one place to look, and a directory provides both. Nothing here forecloses a collector
later: JSON lines on disk is what one would ingest anyway.

## Why exact-value redaction is the half that matters

Pattern matching on `token=`, on bearer credentials and on the shape of a Discord bot
token catches the labelled cases. It does not catch a token that a library interpolated
into a URL, or one that appears in an exception message as an unlabelled string — which
is how a bot token actually leaks. Registering the values the process holds catches those
regardless of how they are spelled or where they appear.

The cost is that the redactor has to be *told*, so `configure()` takes the secrets as an
argument rather than offering a method to call afterwards. An entry point that adds a
credential and forgets to register it is the failure mode, and putting the list in the
same call that sets logging up is the only structural defence available.

Values shorter than six characters are ignored. A secret of `dev` would blank those three
letters everywhere in the log, which destroys far more than it protects.

## Consequences

- **A `logs` service runs first and `chmod 0777 /logs`.** The four containers run as four
  different unprivileged users, and a bind mount arrives owned by whoever owns the host
  directory. Without it, whichever service does not match that owner fails to open its
  file and carries on silently to stdout — the exact failure this exists to prevent. It
  is a permissive mode on a directory that holds no secrets, on a host whose users are
  already whoever can run `docker`.
- **Disk is bounded per service and not in total.** 100MB each: the Python processes
  through `RotatingFileHandler`, nginx through a `logrotate` running inside its own
  container on the same 10MB × 10 numbers. cloudflared rotates itself, which is why it
  is pointed at `TUNNEL_LOGDIRECTORY` rather than `TUNNEL_LOGFILE` — the latter grows
  unbounded. Nothing on the host has to know the bind mount exists.

  Rotating nginx from inside its container rather than from a host `logrotate` entry has
  one weakness worth writing down: the loop that drives it is backgrounded by an
  entrypoint script and nothing supervises it. If it dies, the files grow and nothing
  says so. The alternative is s6 or supervisord in an image whose whole job is serving
  static files. `ls -la logs/` is where it would show.

  Two file modes are load-bearing there, and both fail silently. The entrypoint skips a
  script without the execute bit, and logrotate refuses a configuration file writable by
  group or others — so an ordinary 0664 checkout, carried into the image by `COPY`, turns
  rotation off with no error anywhere. The Dockerfile sets both explicitly.
- **Redaction is deliberately over-broad.** A line reading `session=[REDACTED]` where the
  value was a harmless identifier is a small loss. The patterns are written to never fire
  on bare digits, so Discord snowflakes — which are how anything is traced — survive.
- **`TIMOTHY_LOG_DIR` empty turns the file off** and leaves logging on stdout, which is
  what tests and a bare `timothy-api` outside compose do. A directory that cannot be
  written to does the same, with a warning, rather than refusing to start: a broken log
  path should not be able to take enforcement down.
- **The client log route is a write path a client controls.** It is authenticated, rate
  limited per actor, capped in size by the schema, deduplicated in the browser before it
  is sent, and its level is a two-value enum. The remaining risk is a signed-in member of
  the management guild filling 30 lines a minute with nonsense.
