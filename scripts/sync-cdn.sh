#!/usr/bin/env bash
# Rsync an assembled CDN delta onto the Coolify host volume.
#   sync-cdn.sh blobs <site-dir>    # upload immutable trees (safe before gate)
#   sync-cdn.sh flip <site-dir>     # replace latest/ + releases/ (after gate)
#   sync-cdn.sh push <site-dir>     # blobs + flip
#   sync-cdn.sh rollback            # restore previous latest/ snapshot on host
#
# Env (required):
#   CDN_SSH_HOST   e.g. idleot.com
#   CDN_SSH_USER   default root
#   CDN_SSH_KEY    private key PEM (secret)
# Optional:
#   CDN_REMOTE_ROOT  default /data/idleot-cdn
#
# Host volume is source of truth. content/packs/client/<rev>/launcher/<ver>
# are content-addressed or versioned — --ignore-existing so re-extracts with
# fresh mtimes do not re-upload multi-GB trees. latest/ and releases/ flip
# live traffic and use --delete.
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

# Immutable trees: skip files already on the host (digest/version paths).
push_immutable() {
  local name="$1"
  [[ -d "${site}/${name}" ]] || return 0
  remote "mkdir -p '${CDN_REMOTE_ROOT}/html/${name}'"
  rsync -a --ignore-existing --info=stats2 -e "$RSYNC_SSH" \
    "${site}/${name}/" "${CDN_SSH_USER}@${CDN_SSH_HOST}:${CDN_REMOTE_ROOT}/html/${name}/"
}

# Mutable trees: update in place (no --delete of extras).
push_mutable() {
  local name="$1"
  [[ -d "${site}/${name}" ]] || return 0
  remote "mkdir -p '${CDN_REMOTE_ROOT}/html/${name}'"
  rsync -a --info=stats2 -e "$RSYNC_SSH" \
    "${site}/${name}/" "${CDN_SSH_USER}@${CDN_SSH_HOST}:${CDN_REMOTE_ROOT}/html/${name}/"
}

# Replace one directory wholesale (delete stale files inside it only).
replace_tree() {
  local name="$1"
  [[ -d "${site}/${name}" ]] || return 0
  remote "mkdir -p '${CDN_REMOTE_ROOT}/html/${name}'"
  rsync -a --delete --info=stats2 -e "$RSYNC_SSH" \
    "${site}/${name}/" "${CDN_SSH_USER}@${CDN_SSH_HOST}:${CDN_REMOTE_ROOT}/html/${name}/"
}

push_blobs() {
  for d in content packs client launcher; do
    push_immutable "$d"
  done
  for d in installers assets images; do
    push_mutable "$d"
  done
}

# Drop host client/<rev> and launcher/<ver> not listed in this assemble's history.
prune_host_history() {
  local sources="${site}/latest/sources.json"
  [[ -f "$sources" ]] || return 0
  local wasm_keep launcher_keep
  wasm_keep="$(python3 -c "import json; h=json.load(open(r'''$sources''')).get('history') or {}; print(' '.join(h.get('wasm') or []))")"
  launcher_keep="$(python3 -c "import json; h=json.load(open(r'''$sources''')).get('history') or {}; print(' '.join(h.get('launcher') or []))")"
  remote bash -s <<EOF
set -euo pipefail
HTML='${CDN_REMOTE_ROOT}/html'
prune_one() {
  local sub="\$1"; shift
  local -a keep=("\$@")
  [[ -d "\$HTML/\$sub" ]] || return 0
  local d name k found
  for d in "\$HTML/\$sub"/*; do
    [[ -d "\$d" ]] || continue
    name="\${d##*/}"
    found=0
    for k in "\${keep[@]+"\${keep[@]}"}"; do
      [[ "\$name" == "\$k" ]] && { found=1; break; }
    done
    if [[ \$found -eq 0 ]]; then
      echo "[sync-cdn] pruning \$sub/\$name"
      rm -rf "\$d"
    fi
  done
}
prune_one client ${wasm_keep}
prune_one launcher ${launcher_keep}
EOF
}

flip_manifests() {
  [[ -f "$site/latest/release.json" ]] || { echo "::error::missing $site/latest/release.json"; exit 1; }

  remote bash -s <<EOF
set -euo pipefail
ROOT='${CDN_REMOTE_ROOT}'
mkdir -p "\$ROOT/html" "\$ROOT/previous-latest"
if [[ -d "\$ROOT/html/latest" ]]; then
  rm -rf "\$ROOT/previous-latest"
  mkdir -p "\$ROOT/previous-latest"
  cp -a "\$ROOT/html/latest/." "\$ROOT/previous-latest/"
fi
EOF

  replace_tree latest
  replace_tree releases

  for f in healthz hints.json; do
    if [[ -f "${site}/${f}" ]]; then
      rsync -a -e "$RSYNC_SSH" "${site}/${f}" "${CDN_SSH_USER}@${CDN_SSH_HOST}:${CDN_REMOTE_ROOT}/html/${f}"
    fi
  done

  prune_host_history
  reload_nginx
}

case "$cmd" in
  blobs)
    site="${1:?site directory}"
    [[ -d "$site" ]] || { echo "::error::missing site dir $site"; exit 1; }
    setup_ssh
    remote "mkdir -p '${CDN_REMOTE_ROOT}/html'"
    push_blobs
    echo "[sync-cdn] blobs ok"
    ;;
  flip)
    site="${1:?site directory}"
    [[ -d "$site" ]] || { echo "::error::missing site dir $site"; exit 1; }
    setup_ssh
    flip_manifests
    echo "[sync-cdn] flip ok"
    ;;
  push)
    site="${1:?site directory}"
    [[ -d "$site" ]] || { echo "::error::missing site dir $site"; exit 1; }
    [[ -f "$site/latest/release.json" ]] || { echo "::error::missing $site/latest/release.json"; exit 1; }
    setup_ssh
    remote "mkdir -p '${CDN_REMOTE_ROOT}/html'"
    push_blobs
    flip_manifests
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
    echo "usage: sync-cdn.sh blobs|flip|push <site-dir> | rollback" >&2
    exit 2
    ;;
esac
