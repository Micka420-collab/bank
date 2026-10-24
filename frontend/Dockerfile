# syntax=docker/dockerfile:1.7
FROM node:22-alpine AS build
WORKDIR /src
COPY package.json package-lock.json ./
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/extra_ca; fi; \
    npm ci --no-audit --no-fund
COPY tsconfig.json vite.config.ts index.html ./
COPY public ./public
COPY src ./src
RUN npm run build

# nginx sans privilèges (port 8080, utilisateur 101), aucun module superflu.
FROM nginxinc/nginx-unprivileged:1.29-alpine
# Les correctifs Alpine publiés après l'image de base (curl, openssl, brotli, c-ares…) ; curl n'est pas utilisé
# (le contrôle de santé passe par wget de busybox).
USER root
RUN apk upgrade --no-cache && (apk del --no-cache curl || true)
USER 101
COPY --from=build /src/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY proxy_api.inc /etc/nginx/conf.d/proxy_api.inc
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1
