#!/usr/bin/env bash
# Minimal Coolify API client for releases. Never force-rebuilds.
#   coolify.sh env <app-uuid> <KEY> <VALUE>    set an app env var (compose apps)
#   coolify.sh image <app-uuid> <TAG>          set docker_registry_image_tag (image apps)
#   coolify.sh deploy <app-uuid>               deploy and wait until finished
# Env: COOLIFY_URL, COOLIFY_TOKEN. Output is limited to statuses — no URLs or
# response bodies, since this runs in a public repo.
set -euo pipefail

: "${COOLIFY_URL:?}" "${COOLIFY_TOKEN:?}"
API="${COOLIFY_URL%/}/api/v1"
TIMEOUT="${COOLIFY_DEPLOY_TIMEOUT:-1200}"

call() {
  local method="$1" path="$2" body="${3:-}"
  local args=(-sS -o "$RUNNER_TEMP/coolify.json" -w '%{http_code}' -X "$method"
    -H "Authorization: Bearer ${COOLIFY_TOKEN}" -H "Accept: application/json")
  [[ -n "$body" ]] && args+=(-H "Content-Type: application/json" -d "$body")
  curl "${args[@]}" "${API}${path}"
}

json() { python3 -c "import json,sys; d=json.load(open('$RUNNER_TEMP/coolify.json')); print($1)"; }

cmd="$1"; uuid="$2"; shift 2
case "$cmd" in
  env)
    key="$1"; value="$2"
    body="$(KEY="$key" VALUE="$value" python3 -c 'import json,os; print(json.dumps({"key": os.environ["KEY"], "value": os.environ["VALUE"], "is_preview": False}))')"
    code="$(call PATCH "/applications/${uuid}/envs" "$body")"
    if [[ "$code" == 404 ]]; then code="$(call POST "/applications/${uuid}/envs" "$body")"; fi
    [[ "$code" =~ ^2 ]] || { echo "::error::coolify env ${key} failed (HTTP ${code})"; exit 1; }
    echo "[coolify] ${key} set"
    ;;
  image)
    tag="$1"
    code="$(call PATCH "/applications/${uuid}" "{\"docker_registry_image_tag\": \"${tag}\"}")"
    [[ "$code" =~ ^2 ]] || { echo "::error::coolify image tag failed (HTTP ${code})"; exit 1; }
    echo "[coolify] image tag ${tag}"
    ;;
  deploy)
    code="$(call GET "/deploy?uuid=${uuid}")"
    [[ "$code" =~ ^2 ]] || { echo "::error::coolify deploy failed (HTTP ${code})"; exit 1; }
    dep="$(json "d['deployments'][0]['deployment_uuid']")"
    echo "[coolify] deployment queued"
    deadline=$((SECONDS + TIMEOUT))
    while (( SECONDS < deadline )); do
      sleep 10
      code="$(call GET "/deployments/${dep}")"
      [[ "$code" =~ ^2 ]] || continue
      status="$(json "d.get('status','')")"
      case "$status" in
        finished) echo "[coolify] deployment finished"; exit 0 ;;
        failed|cancelled*|error) echo "::error::coolify deployment ${status}"; exit 1 ;;
      esac
    done
    echo "::error::coolify deployment timed out after ${TIMEOUT}s"; exit 1
    ;;
  *)
    echo "usage: coolify.sh env|image|deploy <uuid> ..." >&2; exit 2 ;;
esac
