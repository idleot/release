#!/usr/bin/env bash
# Fail fast when release pins lack GHCR images (require_images path only).
# Env: REGISTRY, QTCLIENT_SHA (or OTC_SHA compat), optional IDLEOT_CLIENT_SHA / TIP_TAG,
#      CRYSTAL_SHA, CLIENT/SERVER/PORTAL ("true"/"false"). DESKTOP_IMAGE defaults to qtclient-desktop.
# Client tips are qtclient+pack: prefer TIP_TAG or ${IDLEOT_CLIENT_SHA}-host-${QTCLIENT_SHA}.
set -euo pipefail

REGISTRY="${REGISTRY:?}"
DESKTOP_IMAGE="${DESKTOP_IMAGE:-qtclient-desktop}"
QTCLIENT_SHA="${QTCLIENT_SHA:-${OTC_SHA:-}}"
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
  : "${QTCLIENT_SHA:?}"
  tip="${TIP_TAG:-}"
  if [[ -z "$tip" && -n "${IDLEOT_CLIENT_SHA:-}" ]]; then
    tip="${IDLEOT_CLIENT_SHA}-host-${QTCLIENT_SHA}"
  fi
  tip="${tip:-$QTCLIENT_SHA}"
  need qtclient-wasm "$tip" "client wasm tip"
  need "$DESKTOP_IMAGE" "$tip" "client desktop tip"
fi
if [[ "${SERVER:-false}" == "true" ]]; then
  : "${CRYSTAL_SHA:?}"
  need crystalserver "$CRYSTAL_SHA" "game server"
fi
# Portal images are private to idleot/portal; skip imagetools here (Coolify pulls).

exit "$missing"
