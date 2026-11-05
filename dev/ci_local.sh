#!/usr/bin/env bash
# CI's Linux checks, on this machine, before a push.
#
#   dev/ci_local.sh                 every Python CI tests, then the smoke job
#   dev/ci_local.sh 3.12            one Python, then the smoke job
#   dev/ci_local.sh --no-smokes     skip the smoke job
#
# Mirrors .github/workflows/ci.yml: per Python -- ruff, pyright as CI runs it
# (bare: [tool.pyright] include names src AND tests) at the version CI pins,
# the suite; then, on 3.12, the "dev harnesses + meters" job.
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
  # Bare, as CI runs it.  It read `pyright src/` until 2026-10-21, and
  # four type errors in tests passed here and would have failed CI.
  step "$version: pyright $pyright_pin"
  # Pinned to the venv: the pip pyright otherwise resolves imports through
  # whatever `python` is on PATH -- the system's, with other versions -- and
  # passed 6 errors CI failed on (2026-10-26).
  "$py" -m pyright --pythonpath "$py"
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

  # Side by side, TRCC_SMOKE_JOBS at a time (default 6): each harness in a
  # sandbox of its own -- HOME, runtime dir, X display, D-Bus and the dev
  # mock's whole state folder (TRCC_DEV_ROOT), empty, as GitHub's runner has
  # it: the mock's fetcher is offline, so no harness needs the downloaded
  # data.  One after another they took 184 s of a 316 s run (2026-11-05), and
  # they could not overlap: they shared one HOME, one socket, one trcc.json --
  # and dev/.trcc/data, where two of them write the SAME test themes
  # (smoke_rotation_mask, smoke_portrait_854480: the first parallel run
  # failed on exactly that).
  # Each writes its exit status to a file, so no result is lost to the order
  # in which they finish; the verdicts print in the usual order.
  run_one() {   # script display -- its log and status in $sandbox
    local s=$1 name box
    name=$(basename "$s")
    box="$sandbox/$name.d"
    mkdir -p "$box/.config" "$box/run" "$box/dev" && chmod 700 "$box/run"
    env -u WAYLAND_DISPLAY -u DISPLAY QT_QPA_PLATFORM=offscreen \
      HOME="$box" XDG_CONFIG_HOME="$box/.config" XDG_RUNTIME_DIR="$box/run" \
      XDG_DATA_HOME="$box/.local/share" XDG_CACHE_HOME="$box/.cache" \
      TRCC_DEV_ROOT="$box/dev" PYTHONPATH="src:." \
      xvfb-run -n "$2" dbus-run-session -- "$py" "$s" > "$sandbox/$name.log" 2>&1
    echo $? > "$sandbox/$name.rc"
  }

  step "smoke harnesses"
  names=()
  display=100
  for s in dev/smoke_*.py; do
    name=$(basename "$s")
    case "$name" in smoke_real_hardware.py|smoke_anything.py) continue ;; esac
    while [ "$(jobs -rp | wc -l)" -ge "${TRCC_SMOKE_JOBS:-6}" ]; do wait -n || true; done
    run_one "$s" "$display" &
    names+=("$name")
    display=$((display + 1))
  done
  wait
  failed=""
  for name in "${names[@]}"; do
    # information, never a verdict (ci.yml)
    [ "$name" = smoke_reported_bugs.py ] && continue
    if [ "$(cat "$sandbox/$name.rc" 2>/dev/null)" = 0 ]; then
      echo "PASS $name"
    else
      echo "FAIL $name -- last lines:"; tail -15 "$sandbox/$name.log"
      failed="$failed $name"
      # Kept OUTSIDE the sandbox, which the EXIT trap deletes: the full log is
      # the diagnosis.  2026-11-04 a qtgui start-up FAIL lost its log this way
      # and only a stray /tmp output from the harness itself showed the cause.
      kept=${kept:-$(mktemp -d "${TMPDIR:-/tmp}/trcc-ci-local-failed.XXXXXX")}
      cp "$sandbox/$name.log" "$kept/"
      echo "     full log kept: $kept/$name.log"
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
