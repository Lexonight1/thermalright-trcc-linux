#!/usr/bin/env bash
# CI's Linux checks, on this machine, before a push.
#
#   dev/ci_local.sh                 every Python CI tests, then the smoke job
#   dev/ci_local.sh 3.12            one Python, then the smoke job
#   dev/ci_local.sh --no-smokes     skip the smoke job
#
# Mirrors .github/workflows/ci.yml: per Python -- ruff, pyright src/ at the
# version CI pins, the suite; then, on 3.12, the "dev harnesses + meters" job.
# It stops at the first failure.  GitHub's run stays the clean-room check (a
# fresh machine, and Windows); this exists so that run is green the first time.
#
# Each Python runs in its own venv under .venv/<version> (gitignored), built
# once with:  python3.X -m venv .venv/3.X && .venv/3.X/bin/pip install -e ".[dev]"
# A clean venv, not the user site: what the user site happens to hold is what
# CI does not, and that is how a missing dependency hides.
#
# The smoke harnesses open real windows and tray icons here -- ui/qapp.py drops
# QT_QPA_PLATFORM=offscreen, and a tray icon reaches the desktop panel over the
# session D-Bus.  So they run on a private X display and a private D-Bus, with
# HOME and the XDG dirs in a throwaway directory: nothing reaches this desktop
# or a running TRCC daemon's socket.
set -euo pipefail
cd "$(dirname "$0")/.."

workflow=.github/workflows/ci.yml
# The one place the versions live is the workflow; read them, never copy them.
read -r -a all_pythons <<< "$(grep -oP 'python-version: \[\K[^]]+' "$workflow" | tr -d '",')"
pyright_pin=$(grep -oP 'pyright==\K[0-9.]+' "$workflow" | head -1)

smokes=1
pythons=()
for arg in "$@"; do
  case "$arg" in
    --no-smokes) smokes=0 ;;
    3.*) pythons+=("$arg") ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done
[ ${#pythons[@]} -eq 0 ] && pythons=("${all_pythons[@]}")

started=$SECONDS
step() { printf '\n=== %s  [%ds]\n' "$*" $((SECONDS - started)); }

venv_python() {
  local py=".venv/$1/bin/python"
  if [ ! -x "$py" ]; then
    echo "no venv for $1 -- build it: python$1 -m venv .venv/$1 && .venv/$1/bin/pip install -e \".[dev]\"" >&2
    exit 2
  fi
  # CI installs pyright beside the suite; same pin here.
  if [ "$("$py" -m pip show pyright 2>/dev/null | grep -oP '^Version: \K.*')" != "$pyright_pin" ]; then
    "$py" -m pip install -q "pyright==$pyright_pin"
  fi
  echo "$py"
}

step "ruff"
"$(venv_python "${pythons[0]}")" -m ruff check .

for version in "${pythons[@]}"; do
  py=$(venv_python "$version")
  step "$version: pyright $pyright_pin src/"
  "$py" -m pyright src/
  step "$version: tests"
  "$py" -m pytest tests/ --tb=short -q -p no:cacheprovider
done

if [ "$smokes" = 1 ]; then
  py=$(venv_python 3.12)
  sandbox=$(mktemp -d)
  trap 'rm -rf "$sandbox"' EXIT
  mkdir -p "$sandbox/.config" "$sandbox/run" && chmod 700 "$sandbox/run"
  isolated() {
    env -u WAYLAND_DISPLAY -u DISPLAY QT_QPA_PLATFORM=offscreen \
      HOME="$sandbox" XDG_CONFIG_HOME="$sandbox/.config" \
      XDG_RUNTIME_DIR="$sandbox/run" XDG_DATA_HOME="$sandbox/.local/share" \
      XDG_CACHE_HOME="$sandbox/.cache" PYTHONPATH="src:." \
      xvfb-run -a dbus-run-session -- "$@"
  }

  step "smoke harnesses"
  failed=""
  for s in dev/smoke_*.py; do
    name=$(basename "$s")
    case "$name" in
      smoke_real_hardware.py|smoke_anything.py) continue ;;
      smoke_reported_bugs.py)            # information, never a verdict (ci.yml)
        isolated "$py" "$s" > "$sandbox/$name.log" 2>&1 || true
        continue ;;
    esac
    if isolated "$py" "$s" > "$sandbox/$name.log" 2>&1; then
      echo "PASS $name"
    else
      echo "FAIL $name -- last lines:"; tail -15 "$sandbox/$name.log"
      failed="$failed $name"
    fi
  done
  [ -n "$failed" ] && { echo "failing harnesses:$failed" >&2; exit 1; }

  step "log harness under the Windows file policy"
  TRCC_SMOKE_CLOSE_BETWEEN_RECORDS=1 isolated "$py" dev/smoke_log_multiprocess.py

  step "device probe matrix"
  # The skip list is the workflow's, read from it -- one quarantine, one place.
  skips=$(grep -vE '^\s*#' "$workflow" | grep -oP -- '--skip-probe \K\S+' \
    | sed 's/^/--skip-probe /' | tr '\n' ' ')
  # shellcheck disable=SC2086
  isolated "$py" dev/smoke_anything.py --device all $skips

  step "ratchets"
  "$py" dev/decompiler/audit_coverage.py \
    --fail-under "$(grep -oP 'audit_coverage.py --fail-under \K[0-9]+' "$workflow")"
  PYTHONPATH=src "$py" dev/tools/ui_contract.py \
    --max-bypasses "$(grep -oP 'ui_contract.py --max-bypasses \K[0-9]+' "$workflow")"
  PYTHONPATH=src "$py" dev/tools/stale_refs.py --check
fi

step "all green -- ${pythons[*]}$([ "$smokes" = 1 ] && echo ' + smokes')"
