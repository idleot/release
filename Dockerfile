# Thin IdleOT CDN runtime — nginx + config only.
# Site tree lives on the Coolify host at /data/idleot-cdn/html and is rsynced
# by scripts/sync-cdn.sh (mounted read-only at /usr/share/nginx/html).
FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf

# Placeholder so the image can start before the first sync seeds the volume.
RUN mkdir -p /usr/share/nginx/html \
 && printf 'ok\n' > /usr/share/nginx/html/healthz

EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://127.0.0.1/healthz >/dev/null || exit 1
