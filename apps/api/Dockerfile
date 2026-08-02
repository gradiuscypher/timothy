# Build context is the workspace root, so uv can resolve the workspace members.
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS build

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first, so a source change does not invalidate the resolved layer.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=packages/core/pyproject.toml,target=packages/core/pyproject.toml \
    --mount=type=bind,source=apps/api/pyproject.toml,target=apps/api/pyproject.toml \
    --mount=type=bind,source=apps/bot/pyproject.toml,target=apps/bot/pyproject.toml \
    uv sync --locked --no-dev --no-install-workspace --package timothy-api

COPY pyproject.toml uv.lock ./
COPY packages/core packages/core
COPY apps/bot/pyproject.toml apps/bot/pyproject.toml
COPY apps/api apps/api

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --package timothy-api


FROM python:3.13-slim-bookworm AS runtime

RUN groupadd --system timothy && useradd --system --gid timothy --create-home timothy

COPY --from=build --chown=timothy:timothy /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER timothy
WORKDIR /app

EXPOSE 8000
CMD ["timothy-api"]
