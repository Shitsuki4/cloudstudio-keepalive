#!/usr/bin/env bash
set -euo pipefail
umask 077

if [ "$#" -gt 1 ]; then
  printf 'Usage: %s [lifecycle|ide|manual|supervisor]\n' "$0" >&2
  exit 2
fi
source_name="${1:-manual}"
case "$source_name" in
  lifecycle|ide|manual|supervisor) ;;
  *) printf 'Invalid startup source\n' >&2; exit 2 ;;
esac
script_count=0

startup_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
persistent_include="$startup_dir/keepalive-boot.conf"
supervisor_dir="${KEEPALIVE_SUPERVISOR_DIR:-/usr/local/share/supervisor}"
runtime_include="$supervisor_dir/keepalive-boot.conf"

fail_before_log() {
  printf '%s\n' "$1" >&2
  exit 1
}

[ -d "$startup_dir" ] && [ ! -L "$startup_dir" ] || fail_before_log "Startup directory is unavailable or symlinked"
[ -f "$persistent_include" ] && [ ! -L "$persistent_include" ] || fail_before_log "Persistent supervisor include is unavailable or symlinked"
if [ -L "$supervisor_dir" ] || { [ -e "$supervisor_dir" ] && [ ! -d "$supervisor_dir" ]; }; then
  fail_before_log "Supervisor include directory is unavailable or symlinked"
fi
# Freshly built containers ship the supervisord [include] rule without the directory it
# points at. This preflight runs before the log is opened, so aborting here is silent and
# hides start.d entirely; create the directory instead and let the sync below report.
mkdir -p -m 755 -- "$supervisor_dir" 2>/dev/null || true
[ -f "$runtime_include" ] && [ ! -L "$runtime_include" ] || [ ! -e "$runtime_include" ] || fail_before_log "Runtime supervisor include is not a regular file"

mkdir -p "$startup_dir/logs" "$startup_dir/start.d"
[ ! -L "$startup_dir/logs" ] && [ -d "$startup_dir/logs" ] || fail_before_log "Startup logs directory is unsafe"
[ ! -L "$startup_dir/start.d" ] && [ -d "$startup_dir/start.d" ] || fail_before_log "Startup script directory is unsafe"

log_path="$startup_dir/logs/boot.log"
if [ -L "$log_path" ] || { [ -e "$log_path" ] && [ ! -f "$log_path" ]; }; then
  fail_before_log "Startup log is unsafe"
fi
: >> "$log_path" || fail_before_log "Startup log is not writable"
exec >>"$log_path" 2>&1

now() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }
log_event() { printf '%s source=%s %s\n' "$(now)" "$source_name" "$*"; }

write_status() {
  local result="$1"
  local status_path="$startup_dir/last-start.txt"
  local temporary
  if [ -L "$status_path" ] || { [ -e "$status_path" ] && [ ! -f "$status_path" ]; }; then
    log_event "event=failed reason=unsafe_last_start"
    return 1
  fi
  if ! temporary="$(mktemp "$startup_dir/.last-start.XXXXXX")"; then
    log_event "event=failed reason=last_start_temp_create"
    return 1
  fi
  if ! printf '%s source=%s result=%s count=%s\n' "$(now)" "$source_name" "$result" "$script_count" > "$temporary"; then
    rm -f -- "$temporary"
    log_event "event=failed reason=last_start_write"
    return 1
  fi
  if ! chmod 600 "$temporary"; then
    rm -f -- "$temporary"
    log_event "event=failed reason=last_start_mode"
    return 1
  fi
  if ! mv -f -- "$temporary" "$status_path"; then
    rm -f -- "$temporary"
    log_event "event=failed reason=last_start_commit"
    return 1
  fi
}

sync_runtime_include() {
  local temporary
  local persistent_hash runtime_hash
  if [ -L "$supervisor_dir" ] || [ ! -d "$supervisor_dir" ]; then
    log_event "event=runtime_include_skipped reason=supervisor_dir_unavailable"
    return 0
  fi
  persistent_hash="$(sha256sum "$persistent_include" | awk '{print $1}')"
  if [ -e "$runtime_include" ]; then
    [ -f "$runtime_include" ] && [ ! -L "$runtime_include" ] || {
      log_event "event=failed reason=unsafe_runtime_include"
      return 1
    }
    runtime_hash="$(sha256sum "$runtime_include" | awk '{print $1}')"
    if [ "$runtime_hash" != "$persistent_hash" ]; then
      log_event "event=failed reason=runtime_include_mismatch"
      return 1
    fi
    [ "$(stat -c '%a' "$runtime_include")" = "600" ] || {
      log_event "event=failed reason=runtime_include_mode"
      return 1
    }
    log_event "event=runtime_include_verified"
    return 0
  fi

  temporary="$(mktemp "$supervisor_dir/.keepalive-boot.XXXXXX")" || {
    log_event "event=failed reason=runtime_include_tempfile"
    return 1
  }
  cat "$persistent_include" > "$temporary"
  chmod 600 "$temporary"
  if ln -- "$temporary" "$runtime_include" 2>/dev/null; then
    rm -f -- "$temporary"
    log_event "event=runtime_include_created"
    return 0
  fi
  rm -f -- "$temporary"
  [ -f "$runtime_include" ] && [ ! -L "$runtime_include" ] || {
    log_event "event=failed reason=runtime_include_create_race"
    return 1
  }
  runtime_hash="$(sha256sum "$runtime_include" | awk '{print $1}')"
  if [ "$runtime_hash" != "$persistent_hash" ]; then
    log_event "event=failed reason=runtime_include_mismatch"
    return 1
  fi
  [ "$(stat -c '%a' "$runtime_include")" = "600" ] || {
    log_event "event=failed reason=runtime_include_mode"
    return 1
  }
  log_event "event=runtime_include_verified"
}

cgroup_directory() {
  # Container caps live under the cgroup of PID 1, not at the cgroup root: the container
  # shares the host's cgroup namespace, so /sys/fs/cgroup/<self path> is the only place
  # where cpu.max / memory.max / memory.oom.group describe this workspace.
  if [ -n "${KEEPALIVE_CGROUP_DIR:-}" ]; then
    printf '%s' "$KEEPALIVE_CGROUP_DIR"
    return 0
  fi
  local relative=""
  if [ -r /proc/self/cgroup ]; then
    relative="$(awk -F: '$1 == "0" { print $3; exit }' /proc/self/cgroup 2>/dev/null || true)"
  fi
  printf '%s' "/sys/fs/cgroup${relative}"
}

cgroup_value() {
  local value=""
  value="$(cat "$1" 2>/dev/null || true)"
  printf '%s' "${value:-unknown}"
}

log_limits() {
  # Recorded every start: the numbers are the platform's, not ours, and nothing survives a
  # rebuild, so the log is the only place to compare headroom across containers.
  local directory disk
  directory="$(cgroup_directory)"
  log_event "event=limits cpu_max=$(cgroup_value "$directory/cpu.max" | tr ' ' '/') memory_max=$(cgroup_value "$directory/memory.max") memory_swap_max=$(cgroup_value "$directory/memory.swap.max") oom_group=$(cgroup_value "$directory/memory.oom.group") memory_current=$(cgroup_value "$directory/memory.current")"
  disk="$(df -Pk "$startup_dir" 2>/dev/null | awk 'NR == 2 { print $3 "/" $2 "KB" }' || true)"
  log_event "event=limits disk_used_total=${disk:-unknown}"
}

if ! command -v flock >/dev/null 2>&1; then
  log_event "event=failed reason=flock_required"
  write_status "failed" || true
  exit 1
fi
lock_path="$startup_dir/boot.lock"
if [ -L "$lock_path" ] || { [ -e "$lock_path" ] && [ ! -f "$lock_path" ]; }; then
  log_event "event=failed reason=unsafe_lock"
  write_status "failed" || true
  exit 1
fi
exec 9>>"$lock_path"
if ! flock -n 9; then
  log_event "event=skipped reason=startup_already_running"
  exit 0
fi

if ! sync_runtime_include; then
  write_status "failed" || true
  exit 1
fi

script_count=0
log_event "event=started"
log_limits
if ! cd -- "$startup_dir/.."; then
  log_event "event=failed reason=workspace_unavailable"
  write_status "failed" || true
  exit 1
fi
for startup_script in "$startup_dir"/start.d/*.sh; do
  [ -e "$startup_script" ] || continue
  if [ -L "$startup_script" ] || [ ! -f "$startup_script" ]; then
    log_event "event=failed reason=unsafe_startup_script name=$(basename -- "$startup_script")"
    write_status "failed" || true
    exit 1
  fi
  script_count=$((script_count + 1))
  log_event "event=script_started name=$(basename -- "$startup_script")"
  if bash "$startup_script" 9>&-; then
    log_event "event=script_completed name=$(basename -- "$startup_script")"
  else
    exit_code=$?
    log_event "event=failed reason=script_failed name=$(basename -- "$startup_script") exit=$exit_code"
    write_status "failed" || true
    exit "$exit_code"
  fi
done

if [ "$script_count" -eq 0 ]; then
  result=no_services_configured
else
  result=commands_completed
fi
if ! write_status "$result"; then
  exit 1
fi
log_event "event=completed result=$result count=$script_count"
cat "$startup_dir/last-start.txt"
