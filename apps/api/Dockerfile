# Build context is the workspace root, so uv can resolve the workspace members.
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS build

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first, so a source change does not invalidate the resolved layer.
#
# `migration/pyproject.toml` is mounted for the same reason the other manifests are:
# the workspace does not resolve without every member's. The migration is a one-shot
# tool and has no business in a runtime image, and `--package` with
# `--no-install-workspace` is what keeps its code out.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=packages/core/pyproject.toml,target=packages/core/pyproject.toml \
    --mount=type=bind,source=apps/api/pyproject.toml,target=apps/api/pyproject.toml \
    --mount=type=bind,source=apps/bot/pyproject.toml,target=apps/bot/pyproject.toml \
    --mount=type=bind,source=migration/pyproject.toml,target=migration/pyproject.toml \
    uv sync --locked --no-dev --no-install-workspace --package timothy-api

COPY pyproject.toml uv.lock ./
COPY packages/core packages/core
COPY apps/bot/pyproject.toml apps/bot/pyproject.toml
# The migration's manifest, for the same reason as above: the workspace has to
# resolve. Its source is deliberately absent.
COPY migration/pyproject.toml migration/pyproject.toml
COPY apps/api apps/api

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --package timothy-api


FROM python:3.13-slim-bookworm AS runtime

RUN groupadd --system timothy && useradd --system --gid timothy --create-home timothy

# The SQLite file lives here. The directory has to exist in the image, owned by the
# unprivileged user, because Docker seeds a fresh named volume from the image's mount
# point — including its ownership. Created at first mount instead, it would be root's,
# and the backend could not write the database it is the sole writer of.
RUN install -d -o timothy -g timothy /data

COPY --from=build --chown=timothy:timothy /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER timothy
WORKDIR /app

EXPOSE 8000
CMD ["timothy-api"]
