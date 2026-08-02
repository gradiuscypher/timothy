# Build context is web/. The SPA build stage lands in phase 6; for now nginx serves a
# placeholder and, more importantly, already proxies /api to the backend.
FROM nginx:1.31-alpine AS runtime

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY public/ /usr/share/nginx/html/

EXPOSE 80
