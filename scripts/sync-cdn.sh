#!/usr/bin/env bash
# Rsync an assembled CDN delta onto the Coolify host volume.
#   sync-cdn.sh blobs <site-dir>    # upload immutable trees (safe before gate)
#   sync-cdn.sh flip <site-dir>     # replace latest/ + releases/ (after gate)
#   sync-cdn.sh push <site-dir>     # blobs + flip
#   sync-cdn.sh verify              # host tree + docker volume mount healthy
#   sync-cdn.sh rollback            # restore previous latest/ snapshot on host
#
# Env (required):
#   CDN_SSH_HOST   e.g. idleot.com
#   CDN_SSH_USER   default root
# Optional:
#   CDN_SSH_KEY      private key PEM (CI secret). When unset, uses the local
#                    ssh agent / default identity (BatchMode).
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
  if [[ -n "${CDN_SSH_KEY:-}" ]]; then
    umask 077
    printf '%s\n' "$CDN_SSH_KEY" > "$KEY_FILE"
    if ! grep -q 'BEGIN .*PRIVATE KEY' "$KEY_FILE"; then
      echo "::error::CDN_SSH_KEY does not look like a PEM private key"
      exit 1
    fi
    SSH=(ssh -i "$KEY_FILE" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new
      -o BatchMode=yes "${CDN_SSH_USER}@${CDN_SSH_HOST}")
    RSYNC_SSH="ssh -i $KEY_FILE -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o BatchMode=yes"
  else
    echo "[sync-cdn] CDN_SSH_KEY unset — using default ssh identity for ${CDN_SSH_USER}@${CDN_SSH_HOST}"
    SSH=(ssh -o StrictHostKeyChecking=accept-new -o BatchMode=yes "${CDN_SSH_USER}@${CDN_SSH_HOST}")
    RSYNC_SSH="ssh -o StrictHostKeyChecking=accept-new -o BatchMode=yes"
  fi
}

remote() { "${SSH[@]}" "$@"; }

# Coolify names dockerimage apps as <uuid>-<timestamp>, not idleot-cdn.
# Prefer image ghcr.io/idleot/cdn; fall back to name / mount path.
cdn_container_id_cmd() {
  cat <<'EOS'
cid="$(docker ps --format '{{.ID}}\t{{.Names}}\t{{.Image}}' | awk -F'\t' '
  $3 ~ /(^|\/)cdn(:|$)/ || $3 ~ /idleot\/cdn/ { print $1; exit }
  $2 ~ /(^|-)idleot-cdn($|-)/ { print $1; exit }
')"
if [[ -z "$cid" ]]; then
  cid="$(docker ps -q | while read -r id; do
    docker inspect "$id" --format '{{.Id}} {{range .Mounts}}{{println .Source .Destination}}{{end}}' \
      | awk '$2 ~ /idleot-cdn/ && $3 == "/usr/share/nginx/html" { print substr($1,1,12); exit }'
  done)"
fi
EOS
}

reload_nginx() {
  remote bash -s <<EOF
set -euo pipefail
$(cdn_container_id_cmd)
if [[ -n "\${cid:-}" ]]; then
  docker exec "\$cid" nginx -s reload 2>/dev/null || true
fi
EOF
}

# Fail fast when Coolify started the thin image without the host volume mount
# (serves stock nginx welcome + baked /healthz, 404 on /latest/*).
# require_release=1 also demands latest/release.json on host and in the container.
verify_cdn() {
  local require_release="${1:-1}"
  remote bash -s <<EOF
set -euo pipefail
HTML='${CDN_REMOTE_ROOT}/html'
REQUIRE_RELEASE='${require_release}'
$(cdn_container_id_cmd)
if [[ -z "\${cid:-}" ]]; then
  echo "::error::no running idleot/cdn container (Coolify uuid name or ghcr.io/idleot/cdn)"
  docker ps --format '{{.Names}} {{.Image}}' || true
  exit 1
fi
mount_ok="\$(docker inspect "\$cid" --format '{{range .Mounts}}{{println .Source .Destination}}{{end}}' \
  | awk '\$2 == "/usr/share/nginx/html" && \$1 ~ /idleot-cdn/ {print "yes"; exit}')"
if [[ "\$mount_ok" != "yes" ]]; then
  echo "::error::cdn container is not mounting ${CDN_REMOTE_ROOT}/html at /usr/share/nginx/html"
  echo "::error::Coolify custom_docker_run_options must include: -v ${CDN_REMOTE_ROOT}/html:/usr/share/nginx/html:ro"
  echo "::error::Redeploy often drops the mount — restart the app after setting run options."
  docker inspect "\$cid" --format '{{range .Mounts}}{{println .Source "->" .Destination}}{{end}}' || true
  exit 1
fi
if [[ "\$REQUIRE_RELEASE" == "1" ]]; then
  if [[ ! -f "\$HTML/latest/release.json" ]]; then
    echo "::error::host missing \$HTML/latest/release.json — flip/blobs did not land"
    ls -la "\$HTML" 2>/dev/null || true
    exit 1
  fi
  if ! docker exec "\$cid" test -f /usr/share/nginx/html/latest/release.json; then
    echo "::error::volume mounted but container cannot see latest/release.json"
    exit 1
  fi
fi
echo "[sync-cdn] verify ok (mount\${REQUIRE_RELEASE:+ + release.json}) cid=\$cid"
EOF
}

# macOS ships an older rsync without --info=stats2; --stats works everywhere.
RSYNC_STATS=(--stats)

# Immutable trees: skip files already on the host (digest/version paths).
push_immutable() {
  local name="$1"
  [[ -d "${site}/${name}" ]] || return 0
  remote "mkdir -p '${CDN_REMOTE_ROOT}/html/${name}'"
  rsync -a --ignore-existing "${RSYNC_STATS[@]}" -e "$RSYNC_SSH" \
    "${site}/${name}/" "${CDN_SSH_USER}@${CDN_SSH_HOST}:${CDN_REMOTE_ROOT}/html/${name}/"
}

# Mutable trees: update in place (no --delete of extras).
push_mutable() {
  local name="$1"
  [[ -d "${site}/${name}" ]] || return 0
  remote "mkdir -p '${CDN_REMOTE_ROOT}/html/${name}'"
  rsync -a "${RSYNC_STATS[@]}" -e "$RSYNC_SSH" \
    "${site}/${name}/" "${CDN_SSH_USER}@${CDN_SSH_HOST}:${CDN_REMOTE_ROOT}/html/${name}/"
}

# Replace one directory wholesale (delete stale files inside it only).
replace_tree() {
  local name="$1"
  [[ -d "${site}/${name}" ]] || return 0
  remote "mkdir -p '${CDN_REMOTE_ROOT}/html/${name}'"
  rsync -a --delete "${RSYNC_STATS[@]}" -e "$RSYNC_SSH" \
    "${site}/${name}/" "${CDN_SSH_USER}@${CDN_SSH_HOST}:${CDN_REMOTE_ROOT}/html/${name}/"
}

push_blobs() {
  for d in content packs client; do
    push_immutable "$d"
  done
  # launcher/<ver>/ packages are versioned (ignore-existing). Live CIP tips
  # (*-current) must overwrite so /play and the desktop launcher see new WASM.
  if [[ -d "${site}/launcher" ]]; then
    remote "mkdir -p '${CDN_REMOTE_ROOT}/html/launcher'"
    for child in "${site}/launcher"/*; do
      [[ -e "$child" ]] || continue
      name="$(basename "$child")"
      if [[ "$name" == "assets-current" ]]; then
        # Reuse marker = stamps only in the assemble tree; host already has the
        # packed sprites. Never --delete-replace from a stamp-only tree.
        if [[ -f "${site}/.idleot/reuse-assets" ]]; then
          echo "[sync-cdn] assets-current unchanged — leave host tip alone"
        else
          replace_tree "launcher/assets-current"
        fi
      elif [[ -d "$child" && "$name" == *-current ]]; then
        # Never overwrite a live tip with an empty stub (package.json only).
        if ! compgen -G "${child}/*.wasm" >/dev/null \
          && ! compgen -G "${child}/tibia.js" >/dev/null \
          && ! compgen -G "${child}/launcher.js" >/dev/null; then
          echo "[sync-cdn] skip empty tip launcher/${name} (no wasm/js) — leave host alone"
        else
          replace_tree "launcher/${name}"
        fi
      elif [[ -d "$child" ]]; then
        remote "mkdir -p '${CDN_REMOTE_ROOT}/html/launcher/${name}'"
        rsync -a --ignore-existing "${RSYNC_STATS[@]}" -e "$RSYNC_SSH" \
          "${child}/" "${CDN_SSH_USER}@${CDN_SSH_HOST}:${CDN_REMOTE_ROOT}/html/launcher/${name}/"
      else
        # Tip manifests (tibiametadata.json, hints.json, …) must overwrite —
        # --ignore-existing left Soft directoryname stuck on the prior tip.
        case "$name" in
          tibiametadata.json|hints.json|*.json.version)
            rsync -a "${RSYNC_STATS[@]}" -e "$RSYNC_SSH" \
              "$child" "${CDN_SSH_USER}@${CDN_SSH_HOST}:${CDN_REMOTE_ROOT}/html/launcher/"
            ;;
          *)
            rsync -a --ignore-existing "${RSYNC_STATS[@]}" -e "$RSYNC_SSH" \
              "$child" "${CDN_SSH_USER}@${CDN_SSH_HOST}:${CDN_REMOTE_ROOT}/html/launcher/"
            ;;
        esac
      fi
    done
  fi
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
    # CIP live tips (launcher-web-current, tibiaclient-*-current, assets-current)
    # are not version history — never prune them.
    [[ "\$name" == *-current ]] && continue
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
  # If assemble omitted things-* (manifests-only previous), pull host copies
  # into the site tree so --delete does not wipe /play sprite lists.
  if [[ -d "${site}/releases" ]]; then
    remote "ls -d ${CDN_REMOTE_ROOT}/html/releases/things-* 2>/dev/null || true" | while read -r d; do
      [[ -n "$d" ]] || continue
      name="$(basename "$d")"
      if [[ ! -d "${site}/releases/${name}" ]]; then
        echo "[sync-cdn] merge host releases/${name} into site before flip"
        mkdir -p "${site}/releases/${name}"
        rsync -a -e "$RSYNC_SSH" \
          "${CDN_SSH_USER}@${CDN_SSH_HOST}:${d}/" "${site}/releases/${name}/"
      fi
    done
  fi
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
  verify-mount)
    setup_ssh
    verify_cdn 0
    ;;
  verify)
    setup_ssh
    verify_cdn 1
    ;;
  *)
    echo "usage: sync-cdn.sh blobs|flip|push <site-dir> | verify|verify-mount | rollback" >&2
    exit 2
    ;;
esac
