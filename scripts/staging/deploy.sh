#!/usr/bin/env bash
set -euo pipefail
# shellcheck disable=SC1091  # Runtime path is anchored to this script.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

deploy_succeeded=0
mutation_attempted=0
production_before_tmp=""
production_after_build_tmp=""
approved_context=""

cleanup_deploy() {
  local status="$?"
  if (( mutation_attempted == 1 && deploy_succeeded == 0 )); then
    safe_compose_down_if_present || info "staging cleanup failed; manual inspection required"
  fi
  [[ -z "$production_before_tmp" ]] || rm -f -- "$production_before_tmp"
  [[ -z "$production_after_build_tmp" ]] || rm -f -- "$production_after_build_tmp"
  [[ -z "$approved_context" ]] || remove_approved_build_context "$approved_context" || info "approved build context cleanup failed"
  return "$status"
}

arm_deploy_cleanup() {
  trap cleanup_deploy EXIT
}

start_staging_db() {
  mutation_attempted=1
  "${COMPOSE[@]}" up -d db
}

run_staging_verification() {
  "${STAGING_ROOT}/scripts/staging/verify.sh"
}

start_staging_api_and_verify() {
  "${COMPOSE[@]}" up -d api
  wait_container_healthy "$STAGING_PROJECT" api 75 2 || return
  run_staging_verification
}

main() {
  local target_sha="${1:-}" image image_id
  require_sha "$target_sha"
  reject_polluted_environment
  acquire_lock
  "${STAGING_ROOT}/scripts/staging/preflight.sh" "$target_sha" >/dev/null
  require_approved_checkout "$target_sha"
  production_before_tmp="$(mktemp)"
  production_after_build_tmp="$(mktemp)"
  arm_deploy_cleanup
  production_snapshot "$production_before_tmp"
  approved_context="$(create_approved_build_context "$target_sha")"
  image="maoxx-os-staging-api:${target_sha}"
  info "building approved image ${image}"
  build_approved_image "$approved_context" "$image"
  image_id="$(docker image inspect --format '{{.Id}}' "$image")"
  info "approved image created: tag=${image} id=${image_id}"
  production_snapshot "$production_after_build_tmp"
  compare_snapshots "$production_before_tmp" "$production_after_build_tmp"
  require_production_healthy
  require_resources
  mkdir -p "$STAGING_STATE_DIR" "$STAGING_STORAGE"
  chmod 700 "$STAGING_STATE_DIR" "$STAGING_STORAGE"
  install -m 600 "$production_before_tmp" "${STAGING_STATE_DIR}/production-before.jsonl"
  printf '%s\n' "$image_id" > "${STAGING_STATE_DIR}/api-image-id"
  chmod 600 "${STAGING_STATE_DIR}/api-image-id"
  export STAGING_API_IMAGE="$image"
  start_staging_db
  # shellcheck disable=SC2016  # Variables expand inside the database container.
  "${COMPOSE[@]}" exec -T db sh -c 'until pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"; do sleep 2; done'
  "${COMPOSE[@]}" run --rm api alembic upgrade head
  "${COMPOSE[@]}" run --rm api alembic current
  "${COMPOSE[@]}" run --rm api alembic heads
  "${COMPOSE[@]}" run --rm api alembic check
  start_staging_api_and_verify
  deploy_succeeded=1
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
