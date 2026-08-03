# Build context is web/. Two stages: node builds the SPA, nginx serves it and proxies
# /api to the backend. Node does not ship — the runtime is static files and a reverse
# proxy, which is part of why this container holds no secrets at all.

FROM node:24-alpine AS build

WORKDIR /build

# Manifests first, so editing a component does not reinstall the dependency tree.
COPY package.json package-lock.json ./
RUN npm ci

COPY tsconfig.json tsconfig.app.json tsconfig.node.json vite.config.ts index.html ./
COPY src ./src

# Vite copies this verbatim into dist/. It holds IBM Plex Mono, which is committed under
# the SIL OFL; .dockerignore keeps the licensed Berkeley Mono faces out of the context
# entirely, so this cannot pick them up even on the machine that has them.
COPY public ./public

# `npm run build` is `tsc -b && vite build`, so the image fails on a type error rather
# than shipping one. The generated API client is committed, so this needs no backend.
RUN npm run build


FROM nginx:1.31-alpine AS runtime

# nginx rotates nothing by itself, and the two files it writes go into the shared log
# directory alongside services that all bound their own (ADR 0014). Rotating here rather
# than from the host keeps `./logs` a directory the host only ever reads.
RUN apk add --no-cache logrotate

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY logrotate.conf /etc/logrotate.d/timothy
COPY docker-entrypoint.d/40-rotate-logs.sh /docker-entrypoint.d/40-rotate-logs.sh
# Both modes are load-bearing. The entrypoint skips a script without the execute bit and
# only says so in a line nobody reads; logrotate refuses a *configuration* file that is
# writable by group or others and does nothing at all. `COPY` carries whatever mode the
# file has in the working tree, so a 0664 checkout — which is what an ordinary umask
# gives — silently turns rotation off. Found exactly that way.
RUN chmod 0755 /docker-entrypoint.d/40-rotate-logs.sh \
    && chmod 0644 /etc/logrotate.d/timothy

COPY --from=build /build/dist/ /usr/share/nginx/html/

EXPOSE 80
