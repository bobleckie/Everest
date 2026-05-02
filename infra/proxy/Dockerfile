# ============================================================
# Outer reverse proxy — handles TLS termination locally.
# In production this is replaced by Azure App Gateway / nginx with a
# real cert / Caddy / etc. The behavior is intentionally identical so
# the app sees the same X-Forwarded-Proto headers.
# ============================================================
FROM nginx:1.27-alpine AS runtime

RUN apk add --no-cache openssl bash

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY generate-cert.sh /usr/local/bin/generate-cert.sh
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/generate-cert.sh /usr/local/bin/entrypoint.sh

# 8443 = HTTPS, 8080 = HTTP (redirects).
EXPOSE 8080 8443

HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
    CMD wget -q --spider --no-check-certificate https://127.0.0.1:8443/ || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]
