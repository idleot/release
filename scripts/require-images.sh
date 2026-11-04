#!/usr/bin/env bash
# Fail fast when release pins lack GHCR images (require_images path only).
# Env: REGISTRY, OTC_SHA, optional IDLEOT_CLIENT_SHA / TIP_TAG, CRYSTAL_SHA,
#      CLIENT/SERVER/PORTAL ("true"/"false"). DESKTOP_IMAGE defaults to tibia-desktop.
# Client tips are qtclient+pack: prefer TIP_TAG or ${IDLEOT_CLIENT_SHA}-host-${OTC_SHA}.
set -euo pipefail

REGISTRY="${REGISTRY:?}"
DESKTOP_IMAGE="${DESKTOP_IMAGE:-tibia-desktop}"
missing=0

need() {
  local image="$1" tag="$2" label="$3"
  if docker buildx imagetools inspect "${REGISTRY}/${image}:${tag}" >/dev/null 2>&1; then
    echo "[ok] ${image}:${tag}"
  else
    echo "::error::missing ${REGISTRY}/${image}:${tag} (${label}) — pass build_if_missing=true so Release builds them"
    missing=1
  fi
}

if [[ "${CLIENT:-false}" == "true" ]]; then
  : "${OTC_SHA:?}"
  tip="${TIP_TAG:-}"
  if [[ -z "$tip" && -n "${IDLEOT_CLIENT_SHA:-}" ]]; then
    tip="${IDLEOT_CLIENT_SHA}-host-${OTC_SHA}"
  fi
  tip="${tip:-$OTC_SHA}"
  need otclient-wasm "$tip" "client wasm tip"
  need "$DESKTOP_IMAGE" "$tip" "client desktop tip"
fi
if [[ "${SERVER:-false}" == "true" ]]; then
  : "${CRYSTAL_SHA:?}"
  need crystalserver "$CRYSTAL_SHA" "game server"
fi
# Portal images are private to idleot/portal; skip imagetools here (Coolify pulls).

exit "$missing"
