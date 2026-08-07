#!/usr/bin/env bash
set -euo pipefail
# shellcheck disable=SC1091  # Runtime path is anchored to this script.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

reject_polluted_environment
api_id="$(require_single_container "$STAGING_PROJECT" api)"
db_id="$(require_single_container "$STAGING_PROJECT" db)"
STAGING_API_IMAGE="$(docker inspect --format '{{.Config.Image}}' "$api_id")"
export STAGING_API_IMAGE
[[ -z "$(docker ps -q --filter "label=com.docker.compose.project=${STAGING_PROJECT}" --filter 'label=com.docker.compose.service=feishu-worker')" ]] || die "worker must not run"
require_container_compose_label "$api_id" com.docker.compose.project "$STAGING_PROJECT" "API project"
require_container_compose_label "$api_id" com.docker.compose.service api "API service"
require_container_compose_label "$db_id" com.docker.compose.project "$STAGING_PROJECT" "DB project"
require_container_compose_label "$db_id" com.docker.compose.service db "DB service"
[[ "$(docker ps -aq --filter "label=com.docker.compose.project=${STAGING_PROJECT}" | wc -l)" == 2 ]] || die "unexpected staging container exists"
db_networks="$(docker inspect --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{println}}{{end}}' "$db_id" | normalize_line_set)" || die "failed to inspect DB networks"
api_networks="$(docker inspect --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{println}}{{end}}' "$api_id" | normalize_line_set)" || die "failed to inspect API networks"
require_exact_line_set "$db_networks" "maoxx-staging_staging_internal" "DB network isolation failed"
require_exact_line_set "$api_networks" $'maoxx-staging_staging_api\nmaoxx-staging_staging_internal' "API networks are unexpected"
production_container_ids=()
for service in db api feishu-worker; do
  production_id="$(require_single_container "$PRODUCTION_PROJECT" "$service")"
  production_container_ids+=("$production_id")
done
production_network_ids="$(collect_container_network_ids "${production_container_ids[@]}")" || die "failed to collect Production network identities"
staging_network_ids="$(collect_container_network_ids "$db_id" "$api_id")" || die "failed to collect Staging network identities"
production_volume_ids="$(collect_container_volume_names "${production_container_ids[@]}")" || die "failed to collect Production volume identities"
staging_volume_ids="$(collect_container_volume_names "$db_id" "$api_id")" || die "failed to collect Staging volume identities"
require_disjoint_resource_ids networks "$production_network_ids" "$staging_network_ids"
require_disjoint_resource_ids volumes "$production_volume_ids" "$staging_volume_ids"
docker inspect --format '{{json .NetworkSettings.Ports}}' "$db_id" | jq -e 'to_entries | all(.value==null)' >/dev/null || die "DB publishes a host port"
docker inspect --format '{{json .NetworkSettings.Ports}}' "$api_id" | jq -e 'to_entries == [{"key":"8000/tcp","value":[{"HostIp":"127.0.0.1","HostPort":"18000"}]}]' >/dev/null || die "API published port is unexpected"
docker inspect --format '{{json .Mounts}}' "$api_id" | jq -e 'length==1 and .[0].Destination=="/app/storage" and .[0].Source=="/opt/maoxx-os-staging/storage"' >/dev/null || die "API mounts are unexpected"
docker inspect --format '{{json .Mounts}}' "$db_id" | jq -e 'length==1 and .[0].Type=="volume" and .[0].Destination=="/var/lib/postgresql/data" and .[0].Name=="maoxx-staging_postgres_data"' >/dev/null || die "DB mounts are unexpected"
[[ "$(docker inspect --format '{{.Image}}' "$api_id")" == "$(<"${STAGING_STATE_DIR}/api-image-id")" ]] || die "API image ID mismatch"
health="$(curl -fsS http://127.0.0.1:18000/health)"; jq -e '.status=="ok" and .environment=="staging"' <<<"$health" >/dev/null || die "API health failed"
db_health="$(curl -fsS http://127.0.0.1:18000/health/db)"
expected_db="$(sed -n 's/^POSTGRES_DB=//p' "$STAGING_ENV_FILE")"; expected_user="$(sed -n 's/^POSTGRES_USER=//p' "$STAGING_ENV_FILE")"
jq -e --arg db "$expected_db" --arg user "$expected_user" '.status=="ok" and .database==$db and .user==$user' <<<"$db_health" >/dev/null || die "database identity mismatch"
current="$("${COMPOSE[@]}" exec -T api alembic current | sed -n 's/ .*//p')"; heads="$("${COMPOSE[@]}" exec -T api alembic heads | sed -n 's/ .*//p')"
[[ "$current" == "$heads" && -n "$heads" ]] || die "Alembic current/head mismatch"
"${COMPOSE[@]}" exec -T api alembic check | redact
production_snapshot "${STAGING_STATE_DIR}/production-after.jsonl"
compare_snapshots "${STAGING_STATE_DIR}/production-before.jsonl" "${STAGING_STATE_DIR}/production-after.jsonl"
require_production_healthy
info "staging verification passed"
