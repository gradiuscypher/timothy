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
cp .env.example .env  # every variable there is commented; the tokens are required
docker compose up --build
```

No service publishes a port. The Cloudflare Tunnel is the only ingress and its single
origin is `http://web:80`, which serves the SPA and proxies `/api` to the backend.

## Calling the API

Everything but `/health` and `/openapi.json` needs two headers. The bearer token
authenticates the caller; `X-Timothy-Actor` names the Discord user it is speaking for,
and carries no authority of its own — what that user may do is resolved against Discord.

```sh
curl -H "Authorization: Bearer $TIMOTHY_INTERNAL_TOKEN" \
     -H "X-Timothy-Actor: user:242024455190577152" \
     http://localhost/api/pools
```

Guild, user and channel IDs are **strings** in requests and responses. They are 64-bit,
and JavaScript numbers are not.
