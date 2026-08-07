#!/usr/bin/env bash
set -euo pipefail
# shellcheck disable=SC1091  # Runtime path is anchored to this script.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

target_sha="${1:-}"
require_sha "$target_sha"
for command in git docker gh jq realpath ss flock; do require_command "$command"; done
require_exact_path "$PWD" "$STAGING_ROOT"
require_exact_path "$(git rev-parse --show-toplevel)" "$STAGING_ROOT"
reject_polluted_environment
validate_env_file "$STAGING_ENV_FILE"
git fetch --prune origin
git cat-file -e "${target_sha}^{commit}" || die "target commit does not exist"
git merge-base --is-ancestor "$target_sha" origin/main || die "target SHA is not in latest origin/main history"
[[ "$(git rev-parse "$target_sha")" == "$target_sha" ]] || die "target SHA did not resolve exactly"
require_approved_checkout "$target_sha"
verify_ci_success "$target_sha"
[[ ! -L "$STAGING_ROOT" ]] || die "staging root must not be a symlink"
[[ ! -e "$STAGING_STORAGE" || ! -L "$STAGING_STORAGE" ]] || die "storage must not be a symlink"
[[ "$(realpath -m -- "$STAGING_STORAGE")" == "$STAGING_STORAGE" ]] || die "invalid staging storage path"
case "$(realpath -m -- "$STAGING_STORAGE")/" in /opt/maoxx-os/storage/*) die "storage overlaps Production";; esac
ss -ltnH 'sport = :18000' | grep -q . && die "127.0.0.1:18000 is in use"
require_valid_predeploy_staging_state
require_production_healthy
require_resources
pgrep -af 'docker (compose )?build|buildx build' | grep -v "$$" | grep -q . && die "another Docker build process is active"
ps -eo pid=,ppid=,args= | awk -v self="$$" -v parent="$PPID" '$1 != self && $1 != parent && $0 ~ /scripts\/staging\/(deploy|stop)\.sh/ {found=1} END {exit !found}' && die "another staging deploy/stop process is active"
production_snapshot /dev/stdout
info "preflight passed for ${target_sha}"
