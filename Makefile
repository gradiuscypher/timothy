# The commands worth not retyping.
#
# A convenience over README.md, never a second source of truth: every recipe here is the
# command that file documents, and if the two disagree the README is right and this is
# stale. Nothing is hidden behind a recipe that you would not want to run by hand — the
# point is remembering the `jq` incantation, not wrapping the toolchain in a new one.
#
# `make` on its own lists what there is. Recipes that touch the deployment (`up`, `down`,
# `restart`) act on whatever `docker compose` finds, so run them where you mean them.

SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c

WEB := web
COMPOSE := docker compose
SERVICE ?= backend

# `docker compose logs` prefixes every line with the service name, which is not JSON and
# stops `jq` at column 10. `--no-log-prefix` is the fix; `fromjson? // empty` skips any
# line that is not JSON at all rather than aborting the pipe on the first one.
LOGS := $(COMPOSE) logs --no-log-prefix
JSONL := jq -R -r 'fromjson? // empty'

# How many log lines to look back over. `make errors N=2000`.
N ?= 500

.DEFAULT_GOAL := help

.PHONY: help
help: ## List the targets
	@grep -hE '^[a-z][a-zA-Z0-9_-]*:.*?## ' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# -- python ------------------------------------------------------------------

.PHONY: sync
sync: ## Create the workspace venv
	uv sync

.PHONY: fmt
fmt: ## Format the Python
	uv run ruff format .

.PHONY: lint
lint: ## Lint and type check the Python
	uv run ruff check .
	uv run ty check

.PHONY: test
test: ## Run the Python tests
	uv run pytest

# -- web ---------------------------------------------------------------------

.PHONY: web-install
web-install: ## Install the web toolchain
	cd $(WEB) && npm install

.PHONY: web-lint
web-lint: ## Lint and type check the web UI
	cd $(WEB) && npx eslint .
	cd $(WEB) && npx tsc -b --noEmit

.PHONY: web-test
web-test: ## Run the web tests
	cd $(WEB) && npx vitest run

.PHONY: api
api: ## Regenerate the committed API client from the backend's OpenAPI document
	cd $(WEB) && npm run api

.PHONY: dev
dev: ## Serve the SPA, proxying /api to localhost:8000
	cd $(WEB) && npm run dev

# -- everything --------------------------------------------------------------

.PHONY: check
check: fmt lint test web-lint web-test api-clean ## Everything CI would run

# `schema.d.ts` is generated and committed so the contract is reviewable, which only holds
# if a drifted copy is a failure rather than a surprise at the next `npm run api`.
.PHONY: api-clean
api-clean: api ## Fail if the committed API client has drifted
	@git diff --quiet -- $(WEB)/src/api/schema.d.ts \
		|| { echo "schema.d.ts has drifted — commit the regenerated file"; exit 1; }

# -- the stack ---------------------------------------------------------------

.PHONY: up
up: ## Build and start the stack
	$(COMPOSE) up -d --build

.PHONY: down
down: ## Stop the stack, keeping its data
	$(COMPOSE) down

.PHONY: restart
restart: ## Restart one service — SERVICE=backend by default
	$(COMPOSE) restart $(SERVICE)

.PHONY: ps
ps: ## What is running
	$(COMPOSE) ps

# -- logs --------------------------------------------------------------------
#
# For anything older than the daemon's buffer, or any question with a *shape* to it, the
# log store is the better tool and the fields below are searchable there:
#   ssh -L 9428:localhost:9428 you@host && open http://localhost:9428/select/vmui/
# See README.md, "Logs".

.PHONY: logs
logs: ## Follow one service's logs as plain messages
	$(LOGS) -f $(SERVICE) | $(JSONL) '"\(.ts) \(.level) \(.message)"'

.PHONY: errors
errors: ## The last N warnings and errors, with any traceback
	$(LOGS) --tail $(N) $(SERVICE) \
		| $(JSONL) 'select(.level == "ERROR" or .level == "WARNING")
			| "\(.ts) \(.level) \(.message)\(if .exception then "\n\(.exception)" else "" end)"'

.PHONY: jobs-log
jobs-log: ## Every job start and finish, as a timeline
	$(LOGS) --tail $(N) backend \
		| $(JSONL) 'select(.extra.job_id)
			| "\(.ts) job \(.extra.job_id) \(.extra.job_kind) \(if .extra.seconds then "finished in \(.extra.seconds)s" else "started" end)"'

# One job runs at a time, so a `started` with nothing after it is what the queue is on.
# That is the question `run_after` timestamps cannot answer: a sweep of a large guild
# legitimately takes half an hour and looks identical to a wedge until it ends.
.PHONY: jobs-now
jobs-now: ## What the worker is on right now
	@$(LOGS) --tail $(N) backend \
		| $(JSONL) 'select(.extra.job_id) | "\(.ts) \(.message)"' \
		| tail -5

.PHONY: names-log
names-log: ## Follow the user-name backfill
	$(LOGS) -f backend | $(JSONL) 'select(.message | test("backfill")) | .message'

# -- the queue ---------------------------------------------------------------

# Most of a healthy queue is pending: the sweep stages one job per guild across
# SWEEP_INTERVAL by dating them forward, and only those with `run_after` in the past are
# claimable. A large pending count is the design; a large *due* count that is not falling
# is the problem. The web UI says the same thing under Ops → Jobs.
.PHONY: queue
queue: ## Queue depth by kind and status, and what is actually due
	@$(COMPOSE) exec -T backend python -c "import sqlite3; \
db = sqlite3.connect('/data/timothy.db'); \
print('%-24s %-10s %s' % ('kind', 'status', 'count')); \
[print('%-24s %-10s %d' % row) for row in db.execute( \
  'SELECT kind, status, COUNT(*) FROM jobs GROUP BY kind, status ORDER BY kind, status')]; \
print(); \
print('due now:', db.execute( \
  \"SELECT COUNT(*) FROM jobs WHERE status='pending' AND run_after <= datetime('now')\" \
  ).fetchone()[0])"

.PHONY: names
names: ## How many user IDs have a name, and how many are still owed a lookup
	@$(COMPOSE) exec -T backend python -c "import sqlite3; \
db = sqlite3.connect('/data/timothy.db'); \
named = db.execute('SELECT COUNT(*) FROM user_names WHERE name IS NOT NULL').fetchone()[0]; \
missing = db.execute('SELECT COUNT(*) FROM user_names WHERE name IS NULL').fetchone()[0]; \
owed = db.execute('SELECT COUNT(*) FROM (SELECT user_id FROM listings UNION \
  SELECT user_id FROM exceptions UNION SELECT user_id FROM enforcement_outcomes) \
  WHERE user_id NOT IN (SELECT user_id FROM user_names)').fetchone()[0]; \
print('named:  ', named); \
print('no such account:', missing); \
print('never looked up:', owed)"
