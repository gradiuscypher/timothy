# timothy
a discord moderation tool

See [CONTEXT.md](./CONTEXT.md) for the domain language, [PLAN.md](./PLAN.md) for the
rewrite plan, and [docs/adr/](./docs/adr/) for the decisions behind it.

## Development

Requires [uv](https://docs.astral.sh/uv/). Python 3.13 is fetched automatically.

```sh
uv sync              # create the workspace venv
uv run ruff format . # format
uv run ruff check .  # lint
uv run ty check      # type check
uv run pytest        # test
```

## Running

```sh
cp .env.example .env  # fill in the Discord and Cloudflare tunnel tokens
docker compose up --build
```

No service publishes a port. The Cloudflare Tunnel is the only ingress and its single
origin is `http://web:80`, which serves the SPA and proxies `/api` to the backend.
