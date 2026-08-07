#!/usr/bin/env bash
set -euo pipefail
# shellcheck disable=SC1091  # Runtime path is anchored to this script.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

target_sha="${1:-}"
require_sha "$target_sha"
reject_polluted_environment
acquire_lock
"${STAGING_ROOT}/scripts/staging/preflight.sh" "$target_sha" >/dev/null
git checkout --detach "$target_sha"
mkdir -p "$STAGING_STATE_DIR" "$STAGING_STORAGE"
chmod 700 "$STAGING_STATE_DIR" "$STAGING_STORAGE"
production_snapshot "${STAGING_STATE_DIR}/production-before.jsonl"
image="maoxx-os-staging-api:${target_sha}"
docker build --tag "$image" "$STAGING_ROOT"
image_id="$(docker image inspect --format '{{.Id}}' "$image")"
printf '%s\n' "$image_id" > "${STAGING_STATE_DIR}/api-image-id"
chmod 600 "${STAGING_STATE_DIR}/api-image-id"
production_snapshot "${STAGING_STATE_DIR}/production-after-build.jsonl"
compare_snapshots "${STAGING_STATE_DIR}/production-before.jsonl" "${STAGING_STATE_DIR}/production-after-build.jsonl"
require_production_healthy
require_resources
export STAGING_API_IMAGE="$image"
staging_started=0
cleanup_failed_deploy() {
  if (( staging_started == 1 )); then
    "${COMPOSE[@]}" down || true
  fi
}
trap cleanup_failed_deploy ERR
"${COMPOSE[@]}" up -d db
staging_started=1
# shellcheck disable=SC2016  # Variables expand inside the database container.
"${COMPOSE[@]}" exec -T db sh -c 'until pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"; do sleep 2; done'
"${COMPOSE[@]}" run --rm api alembic upgrade head
"${COMPOSE[@]}" run --rm api alembic current
"${COMPOSE[@]}" run --rm api alembic heads
"${COMPOSE[@]}" run --rm api alembic check
"${COMPOSE[@]}" up -d api
"${STAGING_ROOT}/scripts/staging/verify.sh"
trap - ERR
