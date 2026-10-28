#!/usr/bin/env bash
# Update Trestle to the newest release on GitHub: fetch (or clone) the source, then run that
# release's own scripts/install.sh, which installs the service or upgrades it in place.
#
#   scripts/update.sh                  # newest release, into the checkout this script is in
#   scripts/update.sh --release v0.4.0 # a given release
#   scripts/update.sh --dir ~/trestle  # the checkout to use (cloned there if missing)
#   scripts/update.sh --drain          # any other option goes to install.sh (--drain, --no-packs,
#                                      # --venv DIR, --bin-dir DIR, --no-link)
#
# Without a checkout (e.g. a fresh machine) it clones TRESTLE_REPO_URL (default the GitHub
# repository) into --dir (default ~/trestle). It always runs the install script of the release
# being installed, not this copy's, so a release can change how it is installed.
set -euo pipefail

usage() { sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }
say() { printf 'update.sh: %s\n' "$*"; }
die() { printf 'update.sh: %s\n' "$*" >&2; exit 1; }

url="${TRESTLE_REPO_URL:-https://github.com/cbrady321/trestle.git}"
here="$(cd "$(dirname "$0")/.." 2>/dev/null && pwd || true)"
if [ -n "$here" ] && git -C "$here" rev-parse --git-dir >/dev/null 2>&1; then
  dir="$here"
else
  dir="$HOME/trestle"
fi
release=""
pass=()
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage 0 ;;
    --dir) [ $# -ge 2 ] || usage 2; dir="$2"; shift 2 ;;
    --release) [ $# -ge 2 ] || usage 2; release="$2"; shift 2 ;;
    *) pass+=("$1"); shift ;;
  esac
done

if [ -d "$dir/.git" ] || git -C "$dir" rev-parse --git-dir >/dev/null 2>&1; then
  say "fetching releases into $dir"
  git -C "$dir" fetch -q --tags origin || die "could not fetch from origin in $dir"
else
  [ ! -e "$dir" ] || [ -z "$(ls -A "$dir")" ] || die "$dir exists and is not a git checkout"
  say "cloning $url into $dir"
  git clone -q "$url" "$dir"
fi

if [ -z "$release" ]; then
  release="$(git -C "$dir" tag -l 'v*' --sort=-v:refname | head -n 1)"
  [ -n "$release" ] || die "no release tag (v*) in $dir"
fi
git -C "$dir" rev-parse -q --verify "refs/tags/$release^{commit}" >/dev/null \
  || die "no release tag $release"

installer="$(mktemp "${TMPDIR:-/tmp}/trestle-install.XXXXXX")"
trap 'rm -f "$installer"' EXIT
git -C "$dir" show "refs/tags/$release:scripts/install.sh" >"$installer" 2>/dev/null \
  || die "release $release has no scripts/install.sh (it predates the script); install it by hand"
say "running $release's install script"
TRESTLE_REPO="$dir" bash "$installer" "$release" ${pass[@]+"${pass[@]}"}
