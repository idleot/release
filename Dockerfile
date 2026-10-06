# IdleOT CDN bundle — nginx with the full tree baked in (ghcr.io/idleot/cdn).
# The tree is assembled by .github/workflows/bundle.yml (scripts/assemble.py);
# GitHub Pages serves the same tree, so paths are identical on both targets.
FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY site/ /usr/share/nginx/html/

EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://127.0.0.1/healthz >/dev/null || exit 1
