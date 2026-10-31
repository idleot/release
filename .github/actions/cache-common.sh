#!/usr/bin/env bash
# Shared helpers for .github/actions/cache-{restore,save}.
# Sourced from action_path; do not execute directly.
# Paths: workspace/.cache/{vcpkg,ccache} — persistent-style across CI jobs.
set -euo pipefail

cache_kind_has() {
  local kind="$1" want="$2"
  case "$kind" in
    both) return 0 ;;
    "$want") return 0 ;;
    *) return 1 ;;
  esac
}

cache_baseline12() {
  local raw="$1" json="${2:-vcpkg.json}"
  if [[ -z "$raw" && -f "$json" ]]; then
    raw="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("builtin-baseline",""))' "$json" 2>/dev/null || true)"
  fi
  [[ -n "$raw" ]] || { echo "[ci-cache] no vcpkg baseline" >&2; return 1; }
  echo "${raw:0:12}"
}

cache_export_env() {
  local vcpkg_dir="$1" ccache_dir="$2"
  mkdir -p "$vcpkg_dir" "$ccache_dir"
  if [[ -n "${GITHUB_ENV:-}" ]]; then
    {
      echo "VCPKG_BINARY_CACHE_DIR=$vcpkg_dir"
      echo "VCPKG_DEFAULT_BINARY_CACHE=$vcpkg_dir"
      echo "VCPKG_BINARY_SOURCES=clear;files,$vcpkg_dir,readwrite"
      echo "CCACHE_DIR=$ccache_dir"
    } >> "$GITHUB_ENV"
  fi
  export VCPKG_BINARY_CACHE_DIR="$vcpkg_dir"
  export VCPKG_DEFAULT_BINARY_CACHE="$vcpkg_dir"
  export VCPKG_BINARY_SOURCES="clear;files,$vcpkg_dir,readwrite"
  export CCACHE_DIR="$ccache_dir"
}

# Merge oras-pulled vcpkg archive into dir (never wipe warmer Actions content).
cache_oras_pull_vcpkg() {
  local image="$1" tip_tag="$2" dest="$3"
  local ref="$image:$tip_tag"
  command -v oras >/dev/null 2>&1 || {
    echo "[ci-cache] oras missing — skip GHCR pull $ref" >&2
    return 0
  }
  command -v zstd >/dev/null 2>&1 || {
    echo "[ci-cache] zstd missing — skip GHCR pull $ref" >&2
    return 0
  }
  local tmp
  tmp="$(mktemp -d "${RUNNER_TEMP:-/tmp}/vcpkg-oras.XXXXXX")"
  # Public packages: prefer anonymous pull (cross-org tokens 403 public pulls).
  if ! oras pull "$ref" -o "$tmp" 2>"$tmp/err"; then
    echo "[ci-cache] GHCR miss $ref ($(head -c 200 "$tmp/err" 2>/dev/null || true))"
    rm -rf "$tmp"
    return 0
  fi
  local archive
  archive="$(find "$tmp" -maxdepth 1 -type f \( -name '*.tar.zst' -o -name '*.tar.gz' \) | head -1 || true)"
  if [[ -z "$archive" ]]; then
    echo "[ci-cache] GHCR pull had no tar archive" >&2
    rm -rf "$tmp"
    return 0
  fi
  mkdir -p "$dest"
  case "$archive" in
    *.zst) tar -C "$dest" -xf "$archive" ;;
    *) tar -C "$dest" -xzf "$archive" ;;
  esac
  echo "[ci-cache] merged GHCR $ref → $dest"
  rm -rf "$tmp"
}

cache_oras_push_vcpkg() {
  local image="$1" tip_tag="$2" imm_tag="$3" src="$4" token="${5:-}" actor="${6:-github-actions}"
  command -v oras >/dev/null 2>&1 || {
    echo "[ci-cache] oras missing — skip GHCR push" >&2
    return 0
  }
  command -v zstd >/dev/null 2>&1 || {
    echo "[ci-cache] zstd missing — skip GHCR push" >&2
    return 0
  }
  if [[ ! -d "$src" ]] || [[ -z "$(find "$src" -type f -print -quit 2>/dev/null)" ]]; then
    echo "[ci-cache] empty vcpkg dir — skip GHCR push"
    return 0
  fi
  if [[ -n "$token" ]]; then
    echo "$token" | oras login ghcr.io -u "$actor" --password-stdin || true
  fi
  local tmp archive
  tmp="$(mktemp -d "${RUNNER_TEMP:-/tmp}/vcpkg-push.XXXXXX")"
  archive="$tmp/vcpkg.tar.zst"
  tar -C "$src" -cf - . | zstd -T0 -3 -o "$archive"
  local layer=application/vnd.oci.image.layer.v1.tar+zstd
  local art=application/vnd.qtclient.vcpkg.cache.v1+tar
  echo "[ci-cache] pushing $image:$imm_tag"
  (cd "$tmp" && oras push "$image:$imm_tag" --artifact-type "$art" "vcpkg.tar.zst:$layer")
  echo "[ci-cache] retag tip $image:$tip_tag"
  (cd "$tmp" && oras push "$image:$tip_tag" --artifact-type "$art" "vcpkg.tar.zst:$layer")
  rm -rf "$tmp"
  echo "[ci-cache] published $image:$tip_tag (+ $imm_tag)"
}
