# Build context is the workspace root, so uv can resolve the workspace members.
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS build

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=packages/core/pyproject.toml,target=packages/core/pyproject.toml \
    --mount=type=bind,source=apps/api/pyproject.toml,target=apps/api/pyproject.toml \
    --mount=type=bind,source=apps/bot/pyproject.toml,target=apps/bot/pyproject.toml \
    uv sync --locked --no-dev --no-install-workspace --package timothy-bot

COPY pyproject.toml uv.lock ./
COPY packages/core/pyproject.toml packages/core/pyproject.toml
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY apps/bot apps/bot

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --package timothy-bot


FROM python:3.13-slim-bookworm AS runtime

RUN groupadd --system timothy && useradd --system --gid timothy --create-home timothy

COPY --from=build --chown=timothy:timothy /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER timothy
WORKDIR /app

CMD ["timothy-bot"]
