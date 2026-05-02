# ============================================================
# Everest frontend image
# ============================================================
# Multi-stage:
#   stage 1: Node 20 → npm ci → npm run build  (produces /app/build)
#   stage 2: nginx 1.27-alpine → serves /build  on port 80
#
# This image lives BEHIND the outer TLS reverse proxy. It speaks
# plain HTTP on port 80; TLS is terminated by infra/proxy.
# ============================================================

# ─── Stage 1: build ───────────────────────────────────────────────
FROM node:20-alpine AS builder

WORKDIR /app

# Cache npm install separately from source.
COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund --silent

# Now bring in the source and build.
COPY . .
ENV CI=true \
    GENERATE_SOURCEMAP=false \
    NODE_OPTIONS=--max-old-space-size=4096
RUN npm run build


# ─── Stage 2: serve ───────────────────────────────────────────────
FROM nginx:1.27-alpine AS runtime

# Replace the default site so we can do try_files for React Router.
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/build /usr/share/nginx/html

# Healthcheck — nginx must respond on / with a 200/304/301.
HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
    CMD wget -q --spider http://127.0.0.1:80/ || exit 1

EXPOSE 80
