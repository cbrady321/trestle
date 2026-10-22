#!/usr/bin/env bash
# Install Trestle, or upgrade it from one release to the next, from this checkout.
#
#   scripts/install.sh                 # the newest release tag (v*), installed or upgraded
#   scripts/install.sh v0.4.0          # a given release
#   scripts/install.sh --drain         # upgrade: wait for live runs to end instead of refusing
#   scripts/install.sh --no-packs      # without the Docker / pytest / migration packs
#   scripts/install.sh --venv DIR      # the service's virtualenv (default ~/.trestle-venv)
#   scripts/install.sh --bin-dir DIR   # where the `trestle` command is linked (default ~/.local/bin)
#   scripts/install.sh --no-link       # leave `trestle` off PATH (call it by its venv path)
#
# A first install: checks out the release, creates the venv, installs Trestle into it (editable,
# from this checkout), creates the home (`trestle init`) and installs and starts the per-user
# service (`trestle service install`).
# An upgrade (the service is already installed): stops the service first (draining, so no live
# run is cut off), checks out the release, then `trestle service upgrade` reinstalls, migrates
# the home and starts it again. With live runs and no --drain it refuses and changes nothing.
# Either way it links `trestle` into the bin dir so this user's shells find it (a symlink to the
# venv's, so later upgrades keep it current; it never replaces a file it did not link), then ends
# with `trestle service status`. The checkout is left at the release's commit
# (detached); `git checkout master` returns to the branch.
set -euo pipefail

usage() { sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

release=""
venv="${TRESTLE_VENV:-$HOME/.trestle-venv}"
packs=1
drain=0
bin_dir="${TRESTLE_BIN_DIR:-$HOME/.local/bin}"
link=1
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage 0 ;;
    --venv) [ $# -ge 2 ] || usage 2; venv="$2"; shift 2 ;;
    --no-packs) packs=0; shift ;;
    --drain) drain=1; shift ;;
    --bin-dir) [ $# -ge 2 ] || usage 2; bin_dir="$2"; shift 2 ;;
    --no-link) link=0; shift ;;
    -*) echo "install.sh: unknown option $1" >&2; usage 2 ;;
    *) [ -z "$release" ] || usage 2; release="$1"; shift ;;
  esac
done

# TRESTLE_REPO: the checkout to install from (scripts/update.sh sets it); default: this script's
repo="${TRESTLE_REPO:-$(cd "$(dirname "$0")/.." && pwd)}"
git=(git -C "$repo")
say() { printf 'install.sh: %s\n' "$*"; }
die() { printf 'install.sh: %s\n' "$*" >&2; exit 1; }

# --- the release ----------------------------------------------------------------------------------
if [ -n "$("${git[@]}" status --porcelain --untracked-files=no)" ]; then
  die "the checkout $repo has uncommitted changes; commit or set them aside first"
fi
if "${git[@]}" remote get-url origin >/dev/null 2>&1; then
  "${git[@]}" fetch -q --tags origin || say "could not fetch from origin; using the tags already here"
fi
if [ -z "$release" ]; then
  release="$("${git[@]}" tag -l 'v*' --sort=-v:refname | head -n 1)"
  [ -n "$release" ] || die "no release tag (v*) found; name one: scripts/install.sh v0.4.0"
fi
"${git[@]}" rev-parse -q --verify "refs/tags/$release^{commit}" >/dev/null \
  || die "no release tag $release"

# --- the interpreter ------------------------------------------------------------------------------
python="${PYTHON:-python3}"
"$python" -c 'import sys; sys.exit(sys.version_info < (3, 12))' 2>/dev/null \
  || die "Trestle needs Python 3.12 or newer; set PYTHON=/path/to/python3.12"

extras=".[packs]"
[ "$packs" = 1 ] || extras="."
trestle="$venv/bin/trestle"

case "$(uname -s)" in
  Darwin) unit="$HOME/Library/LaunchAgents/dev.trestle.service.plist" ;;
  Linux) unit="$HOME/.config/systemd/user/trestle.service" ;;
  *) die "unsupported platform $(uname -s): the service runs on macOS and Linux" ;;
esac

link_command() {
  local target="$bin_dir/trestle"
  if [ -e "$target" ] && [ ! -L "$target" ]; then
    say "not linking: $target exists and is not a symlink; call $trestle by its path"
    return
  fi
  mkdir -p "$bin_dir"
  ln -sfn "$trestle" "$target"
  say "linked $target -> $trestle"
  case ":$PATH:" in
    *":$bin_dir:"*) ;;
    *) say "$bin_dir is not on PATH; add to your shell profile: export PATH=\"$bin_dir:\$PATH\"" ;;
  esac
}

checkout_release() {
  say "checking out $release"
  "${git[@]}" -c advice.detachedHead=false checkout -q --detach "refs/tags/$release"
}

if [ -f "$unit" ] && [ -x "$trestle" ]; then
  # --- upgrade ----------------------------------------------------------------------------------
  say "upgrading the installed service to $release"
  live="$("$trestle" service status 2>/dev/null | sed -n 's/^live runs: //p' || true)"
  if [ "${live:-0}" != "0" ] && [ "$drain" = 0 ]; then
    die "$live live run(s); nothing changed. Rerun with --drain to wait for them to end"
  fi
  # stop before the checkout moves the code under the running service (an editable install)
  "$trestle" service stop
  checkout_release
  "$trestle" service upgrade --drain
else
  # --- first install ----------------------------------------------------------------------------
  say "installing $release into $venv"
  checkout_release
  [ -x "$venv/bin/python" ] || "$python" -m venv "$venv"
  "$venv/bin/python" -m pip install -q --upgrade pip
  (cd "$repo" && "$venv/bin/python" -m pip install -q -e "$extras")
  "$trestle" init
  "$trestle" service install
fi

[ "$link" = 0 ] || link_command
"$trestle" service status
say "done: $release. Agent hosts connect to the endpoint above with the token in \$TRESTLE_HOME/agent.token"
