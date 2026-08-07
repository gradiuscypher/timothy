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

# `make logs bot`, as well as `make logs SERVICE=bot`.
#
# Make reads a trailing `bot` as a second goal, not as an argument. Unguarded, that ran the
# recipe against the default SERVICE and only *then* failed with "No rule to make target
# 'bot'" — and under `logs -f` you never reach the error, because you interrupt the follow
# first. So the symptom was every service name printing the backend's logs, which is the
# worst way this file could be wrong: quietly answering a question nobody asked.
#
# Only goals after the first are candidates, and the compose lookup happens only when there
# is one — `docker` stays off the path of `make help` and of every target that is not about
# logs. A trailing word that names no service is left for make to reject exactly as before.
#
# `logs` is both a target here and a service in compose, so it is never swallowed: a bare
# `make logs` has to stay the recipe. Reach that service the long way, with SERVICE=logs.
TRAILING := $(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))
ifneq ($(TRAILING),)
NAMED := $(filter-out logs,$(filter $(shell $(COMPOSE) config --services 2>/dev/null),$(TRAILING)))
ifneq ($(NAMED),)
SERVICE := $(firstword $(NAMED))
# Swallow it, so make does not go looking for a target by that name.
$(NAMED):
	@:
endif
endif

# `docker compose logs` prefixes every line with the service name, which is not JSON and
# stops `jq` at column 10. `--no-log-prefix` is the fix; `fromjson? // empty` skips any
# line that is not JSON at all rather than aborting the pipe on the first one.
#
# `JQ` and `LINE` are deliberately *not* a ready-made filter. jq takes exactly one filter
# argument, so a variable holding a quoted one and a recipe adding another quoted one hand
# it two — which is what the first version of this file did. Each recipe below therefore
# builds a single filter, on a single line, and every one of them is exercised by
# `make selftest`.
LOGS := $(COMPOSE) logs --no-log-prefix
JQ := jq -R -r
LINE := fromjson? // empty

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
check: fmt lint test web-lint web-test api-clean selftest ## Everything CI would run

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

# Reads compose.yaml rather than the daemon, so it answers the same whether or not the
# stack is up — which is what you want when the question is "what do I pass to SERVICE=".
.PHONY: services
services: ## The service names, for SERVICE=
	@$(COMPOSE) config --services | sort

# -- logs --------------------------------------------------------------------
#
# For anything older than the daemon's buffer, or any question with a *shape* to it, the
# log store is the better tool and the fields below are searchable there:
#   ssh -L 9428:localhost:9428 you@host && open http://localhost:9428/select/vmui/
# See README.md, "Logs".

.PHONY: logs
logs: ## Follow one service's logs as plain messages
	$(LOGS) -f $(SERVICE) | $(JQ) '$(LINE) | "\(.ts) \(.level) \(.message)"'

.PHONY: errors
errors: ## The last N warnings and errors, with any traceback
	$(LOGS) --tail $(N) $(SERVICE) | $(JQ) '$(LINE) | select(.level == "ERROR" or .level == "WARNING") | "\(.ts) \(.level) \(.message)" + (if .exception then "\n" + .exception else "" end)'

.PHONY: jobs-log
jobs-log: ## Every job start and finish, as a timeline
	$(LOGS) --tail $(N) backend | $(JQ) '$(LINE) | select(.extra.job_id) | "\(.ts) job \(.extra.job_id) \(.extra.job_kind) " + (if .extra.seconds then "finished in \(.extra.seconds)s" else "started" end)'

# One job runs at a time, so a `started` with nothing after it is what the queue is on.
# That is the question `run_after` timestamps cannot answer: a sweep of a large guild
# legitimately takes half an hour and looks identical to a wedge until it ends.
.PHONY: jobs-now
jobs-now: ## What the worker is on right now
	@$(LOGS) --tail $(N) backend | $(JQ) '$(LINE) | select(.extra.job_id) | "\(.ts) \(.message)"' | tail -5

# Needs LOG_LEVEL=debug on the backend: the per-pair line is DEBUG because a fan-out is
# thousands of them. `pairs` is the detail, `jobs-now` is the altitude above it.
.PHONY: pairs
pairs: ## What the worker decided about each (guild, user)
	$(LOGS) --tail $(N) backend | $(JQ) '$(LINE) | select(.extra.decision) | "\(.ts) guild \(.extra.guild_id) user \(.extra.user_id) \(.extra.decision)"'

.PHONY: names-log
names-log: ## Follow the user-name backfill
	$(LOGS) -f backend | $(JQ) '$(LINE) | select(.message | test("backfill")) | .message'

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

# -- proving the recipes work ------------------------------------------------

# The log recipes are shell pipelines built from make variables, and the first version of
# this file shipped one that could not parse: the filters were tested by hand in bash and
# never through `make`, where the quoting is what breaks. This runs each of them for real
# — through make, through the same variables — against a fixture of the lines the backend
# actually writes, with `docker` replaced by a stub that prints it.
#
# Cheap enough to leave in `check`. It needs `jq` and nothing else.
SELFTEST := $(CURDIR)/scripts/selftest-logs.sh

.PHONY: selftest
selftest: ## Run the log recipes against a fixture, through make
	@$(SELFTEST)
