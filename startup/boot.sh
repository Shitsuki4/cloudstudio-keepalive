#!/usr/bin/env bash
set -euo pipefail
umask 077

startup_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
source_name="${1:-manual}"
case "$source_name" in
  lifecycle|ide|manual) ;;
  *) printf 'Invalid startup source\n' >&2; exit 2 ;;
esac

mkdir -p "$startup_dir/logs" "$startup_dir/start.d"
if ! command -v flock >/dev/null 2>&1; then
  printf 'flock is required for safe startup\n' >&2
  exit 1
fi
exec 9>"$startup_dir/boot.lock"
if ! flock -n 9; then
  printf 'Startup already running\n'
  exit 0
fi

exec >>"$startup_dir/logs/boot.log" 2>&1
printf '%s source=%s event=started\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$source_name"
printf '%s source=%s result=running\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$source_name" >"$startup_dir/last-start.txt"
script_count=0
for startup_script in "$startup_dir"/start.d/*.sh; do
  [ -f "$startup_script" ] || continue
  script_count=$((script_count + 1))
  printf 'Starting %s\n' "$(basename -- "$startup_script")"
  if bash "$startup_script" 9>&-; then
    printf 'Completed %s\n' "$(basename -- "$startup_script")"
  else
    exit_code=$?
    printf '%s source=%s event=failed exit=%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$source_name" "$exit_code"
    printf '%s source=%s result=failed exit=%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$source_name" "$exit_code" >"$startup_dir/last-start.txt"
    exit "$exit_code"
  fi
done

if [ "$script_count" -eq 0 ]; then
  result=no_services_configured
else
  result=commands_completed
fi
printf '%s source=%s result=%s count=%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$source_name" "$result" "$script_count" >"$startup_dir/last-start.txt"
cat "$startup_dir/last-start.txt"
