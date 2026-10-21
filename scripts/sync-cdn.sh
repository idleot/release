#!/usr/bin/env bash
# Rsync an assembled CDN site tree onto the Coolify host volume.
#   sync-cdn.sh push <site-dir>     # merge blobs + flip manifests
#   sync-cdn.sh rollback            # restore previous latest/ snapshot on host
#
# Env (required for push/rollback):
#   CDN_SSH_HOST   e.g. idleot.com
#   CDN_SSH_USER   default root
#   CDN_SSH_KEY    private key PEM (secret)
# Optional:
#   CDN_REMOTE_ROOT  default /data/idleot-cdn
#
# Layout on host:
#   $CDN_REMOTE_ROOT/html/     live document root (Coolify bind-mount)
#   $CDN_REMOTE_ROOT/previous-latest/  last latest/ for rollback
#
# content/ and packs/ are merged additively (no --delete) so CI does not need
# to download the multi-GB blob store. Everything else uses --delete.
set -euo pipefail

: "${CDN_SSH_HOST:?}"
CDN_SSH_USER="${CDN_SSH_USER:-root}"
CDN_REMOTE_ROOT="${CDN_REMOTE_ROOT:-/data/idleot-cdn}"
KEY_FILE="${RUNNER_TEMP:-/tmp}/cdn_ssh_key"

cmd="${1:-}"
shift || true

setup_ssh() {
  : "${CDN_SSH_KEY:?}"
  umask 077
  printf '%s\n' "$CDN_SSH_KEY" > "$KEY_FILE"
  if ! grep -q 'BEGIN .*PRIVATE KEY' "$KEY_FILE"; then
    echo "::error::CDN_SSH_KEY does not look like a PEM private key"
    exit 1
  fi
  SSH=(ssh -i "$KEY_FILE" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new
    -o BatchMode=yes "${CDN_SSH_USER}@${CDN_SSH_HOST}")
  RSYNC_SSH="ssh -i $KEY_FILE -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o BatchMode=yes"
}

remote() { "${SSH[@]}" "$@"; }

reload_nginx() {
  remote bash -s <<EOF
cid="\$(docker ps --format '{{.ID}} {{.Names}}' | awk '/idleot-cdn|cdn/ {print \$1; exit}')"
if [[ -n "\$cid" ]]; then
  docker exec "\$cid" nginx -s reload 2>/dev/null || true
fi
EOF
}

case "$cmd" in
  push)
    site="${1:?site directory}"
    [[ -d "$site" ]] || { echo "::error::missing site dir $site"; exit 1; }
    [[ -f "$site/latest/release.json" ]] || { echo "::error::missing $site/latest/release.json"; exit 1; }
    setup_ssh
    remote "mkdir -p '${CDN_REMOTE_ROOT}/html/content' '${CDN_REMOTE_ROOT}/html/packs' '${CDN_REMOTE_ROOT}/previous-latest'"

    # Snapshot live latest/ for rollback before we touch manifests.
    remote bash -s <<EOF
set -euo pipefail
ROOT='${CDN_REMOTE_ROOT}'
if [[ -d "\$ROOT/html/latest" ]]; then
  rm -rf "\$ROOT/previous-latest"
  mkdir -p "\$ROOT/previous-latest"
  cp -a "\$ROOT/html/latest/." "\$ROOT/previous-latest/"
fi
EOF

    # Additive blob merge (never delete host content/packs).
    if [[ -d "$site/content" ]]; then
      rsync -a --info=stats2 -e "$RSYNC_SSH" \
        "${site}/content/" "${CDN_SSH_USER}@${CDN_SSH_HOST}:${CDN_REMOTE_ROOT}/html/content/"
    fi
    if [[ -d "$site/packs" ]]; then
      rsync -a --info=stats2 -e "$RSYNC_SSH" \
        "${site}/packs/" "${CDN_SSH_USER}@${CDN_SSH_HOST}:${CDN_REMOTE_ROOT}/html/packs/"
    fi

    # Manifests + client/assets/launcher/etc. — delete stale files in those trees only.
    rsync -a --delete --info=stats2 \
      --exclude content/ --exclude packs/ \
      -e "$RSYNC_SSH" \
      "${site}/" "${CDN_SSH_USER}@${CDN_SSH_HOST}:${CDN_REMOTE_ROOT}/html/"

    reload_nginx
    echo "[sync-cdn] push ok"
    ;;
  rollback)
    setup_ssh
    remote bash -s <<EOF
set -euo pipefail
ROOT='${CDN_REMOTE_ROOT}'
[[ -d "\$ROOT/previous-latest" ]] || { echo "no previous-latest snapshot"; exit 1; }
rm -rf "\$ROOT/html/latest"
mkdir -p "\$ROOT/html/latest"
cp -a "\$ROOT/previous-latest/." "\$ROOT/html/latest/"
EOF
    reload_nginx
    echo "[sync-cdn] rollback ok"
    ;;
  *)
    echo "usage: sync-cdn.sh push <site-dir> | rollback" >&2
    exit 2
    ;;
esac
