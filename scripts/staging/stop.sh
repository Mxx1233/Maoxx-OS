#!/usr/bin/env bash
set -euo pipefail
# shellcheck disable=SC1091  # Runtime path is anchored to this script.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

reject_polluted_environment
acquire_lock
require_production_healthy
production_snapshot "${STAGING_STATE_DIR}/production-before-stop.jsonl"
ids="$(docker ps -aq --filter "label=com.docker.compose.project=${STAGING_PROJECT}")"
[[ -n "$ids" ]] || die "no exact staging project objects found"
head_sha="$(git -C "$STAGING_ROOT" rev-parse HEAD)"
require_sha "$head_sha"
export STAGING_API_IMAGE="maoxx-os-staging-api:${head_sha}"
while IFS= read -r id; do
  [[ "$(docker inspect --format '{{.Config.Labels.com.docker.compose.project}}' "$id")" == "$STAGING_PROJECT" ]] || die "unexpected project label"
  service="$(docker inspect --format '{{.Config.Labels.com.docker.compose.service}}' "$id")"
  case "$service" in db|api|feishu-worker) ;; *) die "unexpected staging service label";; esac
done <<< "$ids"
"${COMPOSE[@]}" down
require_production_healthy
production_snapshot "${STAGING_STATE_DIR}/production-after-stop.jsonl"
compare_snapshots "${STAGING_STATE_DIR}/production-before-stop.jsonl" "${STAGING_STATE_DIR}/production-after-stop.jsonl"
