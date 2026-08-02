# Phase 0 → Phase 1 handoff

Paste this into a fresh session to pick the rewrite up at phase 1.

---

We're rewriting `banpool-tim-gcp` as **Timothy**, a Python service on Docker Compose with
SQLite. Read `CONTEXT.md` for the domain language, `PLAN.md` for the plan, and
`docs/adr/` for the seven decisions behind it. Those three are the source of truth —
this handoff only records what they don't.

**Phase 0 is complete and committed** at `ae3fc4e` ("phase 0 complete"). Start phase 1.

## What phase 0 built

A uv workspace, three members, no domain code:

- `packages/core` → `timothy_core` — empty but for `__version__`. Deps declared:
  pydantic, SQLAlchemy 2 async, aiosqlite, alembic.
- `apps/api` → `timothy_api` — FastAPI `create_app()` with `/health`, a `Settings` for
  process config, `timothy-api` entry point.
- `apps/bot` → `timothy_bot` — settings plus a `__main__` that health-checks the backend
  and then idles. No gateway client yet; that's phase 4.

Tooling is configured once at the workspace root and inherited: ruff with
`select = ["ALL"]`, ty with `error-on-warning = true`, pytest with
`filterwarnings = ["error"]` and `--strict-config`. `ruff format`, `ruff check`,
`ty check` and `pytest` all pass clean — keep them that way.

Containers: `apps/api/Dockerfile` and `apps/bot/Dockerfile` build from the workspace root
via `uv sync --locked --package <name>`, non-root on `python:3.13-slim`. `web/` is nginx
serving a placeholder with the `/api` → `backend:8000` proxy already wired. `compose.yaml`
runs backend/bot/web/cloudflared with no published ports. CI is `.github/workflows/ci.yml`:
a `python` job and a `stack` job that stands the compose stack up and asserts it works.

All of this was verified running, not just written — images build, the stack comes up,
the bot reaches the backend through the compose network, and nginx proxies `/api`.

## Things decided or discovered in phase 0 that aren't in the docs

- **`filterwarnings = ["error"]` has teeth.** starlette 1.3 deprecates `httpx` in
  `TestClient` and wants `httpx2`, which is why `httpx2` is a dev dependency. Expect
  similar to surface as deps grow; fix the cause rather than loosening the setting.
- **ty's rule names aren't mypy's.** It's `possibly-missing-attribute` and
  `possibly-missing-import`, not `possibly-unbound-*`. Unknown rule names are a warning,
  and `error-on-warning = true` turns that into a CI failure — which is the point.
- **`UV_FROZEN` and `uv sync --locked` are mutually exclusive.** CI uses `--locked` only.
- **The bot's independence from `core` is a convention, not a constraint.** PLAN.md says
  the bot shares no domain logic; today that's enforced by a comment in
  `apps/bot/pyproject.toml` and nothing else. If it matters, phase 4 should add a real
  check — an import-linter contract or a CI grep.
- **nginx listens on IPv4 only**, so an in-container `wget http://localhost/` fails on
  `::1` while `127.0.0.1` works. Irrelevant in production (the compose bridge is IPv4),
  but it will waste your time when checking by hand. CI uses `127.0.0.1` for this reason.
- **Image pins were bumped** to `cloudflared:2026.7.3` and `nginx:1.31-alpine`; both were
  years stale as first written.
- **Only process settings exist so far.** `apps/api/src/timothy_api/settings.py` has host,
  port and log level under the `TIMOTHY_` prefix. Every domain setting in PLAN.md's
  Configuration table — `MANAGEMENT_GUILD_ID`, `DRY_RUN`, `ENFORCEMENT_BURST_LIMIT`,
  `SWEEP_INTERVAL`, `PERMISSION_CACHE_TTL` — is unimplemented. `DRY_RUN` in particular has
  a fail-safe requirement (unparseable means on, per ADR 0007); honour it wherever it lands.

## Carried forward

- **GitHub Actions layer caching.** The `stack` job rebuilds all three images from scratch
  every run. Deferred deliberately; there's a comment marking the spot in `ci.yml`.
  Revisit when builds get slow.
- **`web/` is a placeholder.** No Vite, no React, no `package.json` — just `nginx.conf`
  and a static `index.html`. The real SPA is phase 6.
- **No `migration/` directory yet.** Phase 5.

## Phase 1: domain core

Per PLAN.md, this is where correctness is won, and none of it touches the network:

1. The schema from PLAN.md's Schema section, as SQLAlchemy 2 models, plus Alembic
   migrations. Note the deliberate choices there: pools use a surrogate key so they can be
   renamed; `enforcement_outcomes` is both audit trail and warn-dedupe key.
2. The `DiscordPort` protocol (ADR 0007) — ban, unban, fetch member, resolve a member's
   guild permissions, post a message — and its in-memory fake, which must simulate rate
   limits, missing members and partial failures.
3. Pure enforcement decision logic: given a user, a guild, and the current listings,
   subscriptions and exceptions, what should happen? Fully tested, no network, no mocking
   of discord.py internals.

**The domain layer must never import discord.py.** That's ADR 0007's central consequence.

Read ADRs 0002, 0004, 0005 and 0006 before writing the decision logic — global-pool
opt-out, immediate and reactive enforcement, attribution-gated reverts, and guild-wide
exceptions each constrain it directly.
