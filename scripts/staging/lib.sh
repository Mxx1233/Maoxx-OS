#!/usr/bin/env bash
set -euo pipefail

readonly STAGING_ROOT="/opt/maoxx-os-staging"
readonly STAGING_PROJECT="maoxx-staging"
readonly STAGING_POSTGRES_VOLUME="${STAGING_PROJECT}_postgres_data"
readonly STAGING_POSTGRES_VOLUME_LABEL="postgres_data"
readonly STAGING_ENV_FILE="${STAGING_ROOT}/.env.staging"
readonly STAGING_COMPOSE_FILE="${STAGING_ROOT}/compose.staging.yml"
# shellcheck disable=SC2034  # Shared by scripts that source this library.
readonly STAGING_STORAGE="${STAGING_ROOT}/storage"
# shellcheck disable=SC2034  # Shared by scripts that source this library.
readonly STAGING_STATE_DIR="${STAGING_ROOT}/.staging-operations"
readonly STAGING_LOCK_FILE="/tmp/maoxx-staging.lock"
readonly PRODUCTION_PROJECT="maoxx-os"
readonly REQUIRED_CHECK="CI / Quality Gate"
# shellcheck disable=SC2034  # Shared by scripts that source this library.
readonly -a COMPOSE=(docker compose --project-directory "${STAGING_ROOT}" --env-file "${STAGING_ENV_FILE}" -p "${STAGING_PROJECT}" -f "${STAGING_COMPOSE_FILE}")

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
info() { printf '%s\n' "$*" >&2; }
require_command() { command -v "$1" >/dev/null 2>&1 || die "required command unavailable: $1"; }
require_sha() { [[ "${1:-}" =~ ^[0-9a-f]{40}$ ]] || die "SHA must be 40 lowercase hexadecimal characters"; }
require_exact_path() { [[ "$(realpath -e -- "$1")" == "$2" ]] || die "unexpected path: $1"; }

require_approved_checkout() {
  local target_sha="$1" root="${2:-$STAGING_ROOT}" path
  local -a critical_paths=(
    scripts/staging/deploy.sh scripts/staging/lib.sh
    scripts/staging/preflight.sh scripts/staging/verify.sh
    scripts/staging/stop.sh compose.staging.yml Dockerfile
    app alembic alembic.ini requirements.txt
  )
  require_sha "$target_sha"
  [[ "$(git -C "$root" rev-parse HEAD)" == "$target_sha" ]] || die "current HEAD does not equal approved target SHA"
  [[ -z "$(git -C "$root" status --porcelain=v1 --untracked-files=all)" ]] || die "deployment working tree must be completely clean"
  for path in "${critical_paths[@]}"; do
    git -C "$root" cat-file -e "${target_sha}:${path}" 2>/dev/null || die "deployment-critical path is absent from approved tree: $path"
    [[ -e "$root/$path" ]] || die "deployment-critical path is absent from working tree: $path"
  done
}

remove_approved_build_context() {
  local context="${1:-}" temp_root canonical
  [[ -n "$context" && -e "$context" ]] || return 0
  temp_root="$(realpath -e -- "${TMPDIR:-/tmp}")" || return 1
  canonical="$(realpath -e -- "$context")" || return 1
  [[ "$(dirname -- "$canonical")" == "$temp_root" && "$(basename -- "$canonical")" =~ ^maoxx-staging-approved\.[A-Za-z0-9]+$ ]] || {
    info "refusing to remove unexpected build context path"
    return 1
  }
  [[ -d "$canonical" && ! -L "$context" ]] || {
    info "refusing to remove non-directory build context"
    return 1
  }
  chmod -R u+w -- "$canonical"
  rm -rf -- "$canonical"
}

create_approved_build_context() {
  local target_sha="$1" root="${2:-$STAGING_ROOT}" context path
  local -a required_files=(Dockerfile alembic.ini requirements.txt)
  local -a required_directories=(app alembic)
  require_sha "$target_sha"
  context="$(mktemp -d "${TMPDIR:-/tmp}/maoxx-staging-approved.XXXXXXXXXX")"
  if ! git -C "$root" archive --format=tar "$target_sha" -- Dockerfile app alembic alembic.ini requirements.txt \
    | tar --extract --file=- --directory="$context" --no-same-owner --no-same-permissions; then
    remove_approved_build_context "$context"
    die "failed to create approved Git build context"
  fi
  for path in "${required_files[@]}"; do
    [[ -f "$context/$path" && ! -L "$context/$path" ]] || {
      remove_approved_build_context "$context"
      die "approved build context is missing required file: $path"
    }
  done
  for path in "${required_directories[@]}"; do
    [[ -d "$context/$path" && ! -L "$context/$path" ]] || {
      remove_approved_build_context "$context"
      die "approved build context is missing required directory: $path"
    }
  done
  [[ ! -e "$context/.git" && ! -e "$context/.env.staging" && ! -e "$context/storage" ]] || {
    remove_approved_build_context "$context"
    die "forbidden path entered approved build context"
  }
  [[ -z "$(find "$context" -type l -print -quit)" ]] || {
    remove_approved_build_context "$context"
    die "symlinks are forbidden in approved build context"
  }
  if ! chmod -R a-w -- "$context"; then
    remove_approved_build_context "$context"
    die "failed to make approved build context read-only"
  fi
  info "approved Git build context created for ${target_sha}"
  printf '%s\n' "$context"
}

build_approved_image() {
  local context="$1" image="$2"
  [[ -d "$context" && ! -L "$context" ]] || die "approved build context is unavailable"
  [[ -f "$context/Dockerfile" && ! -L "$context/Dockerfile" ]] || die "approved Dockerfile is unavailable"
  docker build --file "$context/Dockerfile" --tag "$image" "$context"
}

reject_polluted_environment() {
  local name
  while IFS='=' read -r name _; do
    case "$name" in
      DATABASE_URL|POSTGRES_*|FEISHU_*|APP_ENV|COMPOSE_FILE|COMPOSE_PROJECT_NAME|COMPOSE_PROFILES)
        die "parent environment contains forbidden variable: ${name}" ;;
    esac
  done < <(env)
}

validate_env_file() {
  local file="$1" root="${2:-$STAGING_ROOT}" owner mode links key value
  local -A values=()
  [[ -f "$file" && ! -L "$file" ]] || die "environment file must be a regular non-symlink"
  links="$(stat -c '%h' -- "$file")"; [[ "$links" == 1 ]] || die "environment file must not be hard-linked"
  owner="$(stat -c '%u' -- "$file")"; [[ "$owner" == "$(id -u)" || "$owner" == 0 ]] || die "invalid environment file owner"
  mode="$(stat -c '%a' -- "$file")"; [[ "$mode" == 600 ]] || die "environment file mode must be 0600"
  git -C "$root" ls-files --error-unmatch "$(realpath --relative-to="$root" "$file")" >/dev/null 2>&1 && die ".env.staging must not be tracked"
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" =~ ^([A-Z][A-Z0-9_]*)=([^[:cntrl:]]*)$ ]] || die "invalid non-executable env-file syntax"
    key="${BASH_REMATCH[1]}"; value="${BASH_REMATCH[2]}"
    # shellcheck disable=SC2016  # Match literal command-substitution syntax.
    [[ "$value" != *'$('* && "$value" != *'`'* ]] || die "command syntax forbidden in env file"
    case "$key" in POSTGRES_DB|POSTGRES_USER|POSTGRES_PASSWORD|DATABASE_URL|DEFAULT_USER_ID|APP_ENV|APP_TIMEZONE|LOCAL_STORAGE_ROOT|FEISHU_APP_ID|FEISHU_APP_SECRET) ;; *) die "unexpected env key: $key" ;; esac
    [[ ! -v "values[$key]" ]] || die "duplicate env key: $key"
    values["$key"]="$value"
  done < "$file"
  for key in POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD DATABASE_URL DEFAULT_USER_ID APP_ENV APP_TIMEZONE LOCAL_STORAGE_ROOT FEISHU_APP_ID FEISHU_APP_SECRET; do
    [[ -v "values[$key]" && -n "${values[$key]}" ]] || die "missing or empty env key: $key"
  done
  [[ "${values[APP_ENV]}" == staging ]] || die "APP_ENV must be staging"
  [[ "${values[LOCAL_STORAGE_ROOT]}" == /app/storage ]] || die "LOCAL_STORAGE_ROOT must be /app/storage"
  [[ "${values[POSTGRES_DB]}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || die "POSTGRES_DB has invalid format"
  [[ "${values[POSTGRES_USER]}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || die "POSTGRES_USER has invalid format"
  [[ "${values[DATABASE_URL]}" =~ ^postgresql\+psycopg://([^:/@]+):[^@]+@db:5432/${values[POSTGRES_DB]}$ ]] || die "DATABASE_URL must target the staging db service"
  [[ "${BASH_REMATCH[1]}" == "${values[POSTGRES_USER]}" ]] || die "DATABASE_URL username must equal POSTGRES_USER"
}

acquire_lock() { exec 9>"$STAGING_LOCK_FILE"; flock -n 9 || die "another staging operation holds the lock"; }

project_container_ids() {
  docker ps -aq --filter "label=com.docker.compose.project=${1}" --filter "label=com.docker.compose.service=${2}"
}
require_single_container() {
  local ids count
  ids="$(project_container_ids "$1" "$2")" || die "failed to query $1/$2 containers"
  count="$(printf '%s\n' "$ids" | sed '/^$/d' | wc -l)"
  [[ "$count" == 1 ]] || die "expected exactly one $1/$2 container"
  printf '%s\n' "$ids"
}
wait_container_healthy() {
  local project="$1" service="$2" attempts="${3:-75}" interval="${4:-2}"
  local container_id inspection state health extra attempt
  [[ "$attempts" =~ ^[1-9][0-9]*$ ]] || die "health wait attempts must be a positive integer"
  [[ "$interval" =~ ^[0-9]+$ ]] || die "health wait interval must be a non-negative integer"
  container_id="$(require_single_container "$project" "$service")" || die "failed to discover $project/$service container"
  for (( attempt = 1; attempt <= attempts; attempt++ )); do
    inspection="$(docker inspect --format '{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$container_id")" || die "failed to inspect $project/$service health"
    IFS='|' read -r state health extra <<< "$inspection"
    [[ -z "$extra" && -n "$state" && -n "$health" ]] || die "invalid $project/$service health inspection"
    if [[ "$state" != running ]]; then
      die "$project/$service exited before becoming healthy"
    fi
    case "$health" in
      healthy) return 0 ;;
      starting) ;;
      unhealthy) die "$project/$service became unhealthy" ;;
      *) die "unexpected $project/$service health status" ;;
    esac
    (( attempt == attempts )) || sleep "$interval"
  done
  die "timed out waiting for $project/$service health"
}
require_container_compose_label() {
  local id="$1" key="$2" expected="$3" description="$4" actual
  case "$key" in
    com.docker.compose.project|com.docker.compose.service) ;;
    *) die "unsupported Docker Compose label key: $key" ;;
  esac
  actual="$(docker inspect --format "{{index .Config.Labels \"${key}\"}}" "$id")" || die "failed to inspect $description label"
  [[ "$actual" == "$expected" ]] || die "wrong $description label"
}
normalize_line_set() { sed '/^[[:space:]]*$/d' | LC_ALL=C sort -u; }
require_exact_line_set() { [[ "$1" == "$2" ]] || die "$3"; }
collect_container_network_ids() {
  local id output raw=""
  (( $# > 0 )) || {
    info "no containers supplied for network identity collection"
    return 1
  }
  for id in "$@"; do
    if ! output="$(docker inspect --format '{{range $k,$v := .NetworkSettings.Networks}}{{$v.NetworkID}}{{println}}{{end}}' "$id")"; then
      info "failed to inspect container network identities"
      return 1
    fi
    raw+=$'\n'"$output"
  done
  printf '%s' "$raw" | normalize_line_set
}
collect_container_volume_names() {
  local id output raw=""
  (( $# > 0 )) || {
    info "no containers supplied for volume identity collection"
    return 1
  }
  for id in "$@"; do
    if ! output="$(docker inspect --format '{{range .Mounts}}{{if eq .Type "volume"}}{{.Name}}{{println}}{{end}}{{end}}' "$id")"; then
      info "failed to inspect container volume identities"
      return 1
    fi
    raw+=$'\n'"$output"
  done
  printf '%s' "$raw" | normalize_line_set
}
require_valid_predeploy_staging_state() {
  local containers networks project_volumes named_volumes project_label volume_label
  containers="$(docker ps -aq --filter "label=com.docker.compose.project=${STAGING_PROJECT}")" || die "failed to query staging containers"
  networks="$(docker network ls -q --filter "label=com.docker.compose.project=${STAGING_PROJECT}")" || die "failed to query staging networks"
  project_volumes="$(docker volume ls -q --filter "label=com.docker.compose.project=${STAGING_PROJECT}")" || die "failed to query staging project volumes"
  named_volumes="$(docker volume ls -q --filter "name=^${STAGING_PROJECT}_")" || die "failed to query staging-named volumes"
  [[ -z "$containers" ]] || die "a staging container already exists"
  [[ -z "$networks" ]] || die "a staging network already exists"
  if [[ -z "$project_volumes" && -z "$named_volumes" ]]; then
    return 0
  fi
  [[ "$project_volumes" == "$STAGING_POSTGRES_VOLUME" && "$named_volumes" == "$STAGING_POSTGRES_VOLUME" ]] || die "unexpected staging volume state"
  project_label="$(docker volume inspect --format '{{index .Labels "com.docker.compose.project"}}' "$STAGING_POSTGRES_VOLUME")" || die "failed to inspect retained staging volume project label"
  volume_label="$(docker volume inspect --format '{{index .Labels "com.docker.compose.volume"}}' "$STAGING_POSTGRES_VOLUME")" || die "failed to inspect retained staging volume identity label"
  [[ "$project_label" == "$STAGING_PROJECT" ]] || die "wrong retained staging volume project label"
  [[ "$volume_label" == "$STAGING_POSTGRES_VOLUME_LABEL" ]] || die "wrong retained staging volume identity label"
}

staging_project_resources_exist() {
  local containers networks volumes
  containers="$(docker ps -aq --filter "label=com.docker.compose.project=${STAGING_PROJECT}")" || {
    info "failed to query staging containers"
    return 2
  }
  networks="$(docker network ls -q --filter "label=com.docker.compose.project=${STAGING_PROJECT}")" || {
    info "failed to query staging networks"
    return 2
  }
  volumes="$(docker volume ls -q --filter "label=com.docker.compose.project=${STAGING_PROJECT}")" || {
    info "failed to query staging volumes"
    return 2
  }
  [[ -n "$containers" || -n "$networks" || -n "$volumes" ]] && return 0
  return 1
}

safe_compose_down_if_present() {
  local status
  if staging_project_resources_exist; then
    info "cleaning up failed staging mutation; PostgreSQL volume will be retained"
    "${COMPOSE[@]}" down
  else
    status="$?"
    (( status == 1 )) || return "$status"
  fi
}

require_disjoint_resource_ids() {
  local kind="$1" production_ids="$2" staging_ids="$3" overlap
  overlap="$(comm -12 <(printf '%s\n' "$production_ids" | sed '/^$/d' | sort -u) <(printf '%s\n' "$staging_ids" | sed '/^$/d' | sort -u))"
  [[ -z "$overlap" ]] || die "Production and Staging ${kind} identities overlap"
}

production_snapshot() {
  local output="$1" service id
  : > "$output"
  for service in db api feishu-worker; do
    id="$(require_single_container "$PRODUCTION_PROJECT" "$service")"
    docker inspect --format '{{json .}}' "$id" | jq -c --arg service "$service" '{service:$service,id:.Id,image_id:.Image,created:.Created,started_at:.State.StartedAt,restart_count:.RestartCount,health:.State.Health.Status,network_ids:(.NetworkSettings.Networks|to_entries|map({key:.key,id:.value.NetworkID})|sort_by(.key)),mounts:(.Mounts|map({type:.Type,source:.Source,destination:.Destination,name:.Name})|sort_by(.destination)),ports:.NetworkSettings.Ports,project:.Config.Labels["com.docker.compose.project"]}' >> "$output"
  done
}
require_production_healthy() {
  local service id health
  for service in db api feishu-worker; do
    id="$(require_single_container "$PRODUCTION_PROJECT" "$service")"
    health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$id")"
    [[ "$health" == healthy ]] || die "Production $service is not healthy"
  done
}
compare_snapshots() { cmp -s -- "$1" "$2" || die "Production invariants changed"; }

require_resources() {
  local mem swap root docker_root docker_avail
  mem="$(awk '/MemAvailable:/{print $2*1024}' /proc/meminfo)"; (( mem >= 1073741824 )) || die "MemAvailable is below 1 GiB"
  swap="$(awk '/SwapFree:/{print $2*1024}' /proc/meminfo)"; (( swap >= 1073741824 )) || die "SwapFree is below 1 GiB"
  root="$(df -PB1 / | awk 'NR==2{print $4}')"; (( root >= 5368709120 )) || die "root filesystem has less than 5 GiB free"
  docker_root="$(docker info --format '{{.DockerRootDir}}')"; docker_avail="$(df -PB1 "$docker_root" | awk 'NR==2{print $4}')"
  (( docker_avail >= 5368709120 )) || die "Docker data filesystem has less than 5 GiB free"
}

redact() { sed -E 's#(postgresql[^:]*://)[^@[:space:]]+@#\1[REDACTED]@#g; s/(PASSWORD|SECRET|TOKEN)=([^[:space:]]+)/\1=[REDACTED]/g'; }

verify_ci_success() {
  local sha="$1" json run_id
  json="$(gh api "repos/Mxx1233/Maoxx-OS/actions/runs?head_sha=${sha}&per_page=100")"
  run_id="$(latest_successful_ci_run_id "$json" "$sha")" || die "latest CI workflow run/attempt is not successful"
  json="$(gh api "repos/Mxx1233/Maoxx-OS/commits/${sha}/check-runs?per_page=100")"
  jq -e --arg sha "$sha" --arg name "$REQUIRED_CHECK" --arg run_id "$run_id" '[.check_runs[] | select(.head_sha==$sha and .name==$name and (.details_url | contains("/actions/runs/"+$run_id+"/")))] | sort_by(.id) | last as $r | ($r != null and $r.status=="completed" and $r.conclusion=="success")' <<<"$json" >/dev/null || die "required CI check is not successful for latest run"
}

latest_successful_ci_run_id() {
  local json="$1" sha="$2"
  jq -er --arg sha "$sha" '[.workflow_runs[] | select(.head_sha==$sha and .name=="CI")] | sort_by(.id,.run_attempt) | last | select(.status=="completed" and .conclusion=="success") | .id' <<<"$json"
}
