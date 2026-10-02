#!/bin/zsh
# ci_parity.sh -- run CI's checks locally, in CI's shape, before a push and before any host session.
# Every check maps to a CI failure that cost a host session (projects/workflow-runtime/FINAL-REPORT.md
# section 6): a check that passes here and fails in CI is a bug in this script.
#
# usage: scripts/ci_parity.sh TIER [WT] [BASE]
#   TIER 0  ~30 s      tool versions against constraints/ci.txt, ruff, ruff format, mypy, the shard
#                      map, the drift rules                                        (every push)
#   TIER 1  ~8-15 min  + the pins/proof/fence/planted shards, per-file isolation of what the diff
#                      touches, the proof-ledger no-install step, the court's selftests on the
#                      landing merge (HEAD merged into BASE)                       (every push)
#   TIER 2  ~45-60 min + every shard, and every CK drill part in a Linux container held to 4 CPUs,
#                      the runner's shape                                (before a host session)
#   WT    the worktree to check (default: cwd). Its COMMITTED HEAD is checked, in a clone under
#         $OUT: uncommitted changes are not seen, and the worktree is never written.
#   BASE  what the PR merges into (default origin/master, as WT names it).
# Exit 0 only if every step passed. Output: $OUT/<step>.log and one verdict line per step.
#
# CI's environment, reproduced for every test step: no GH_TOKEN/GITHUB_TOKEN and an empty gh
# config (no live GitHub read can pass here and fail there), the guards' clean PATH, the
# `ci-test` proof gate, GITHUB_EVENT_NAME=pull_request (G-E2 reports a head with no host-docker
# record as pending, as on a PR run), and the release graft CI's history-reading jobs apply.
#
# Environment:
#   PARITY_PY     interpreter holding CI's exact tool set (built with
#                 `pip install -c constraints/ci.txt -e ".[dev,packs,env]"`; tier 0 checks it)
#   NOINSTALL_PY  a bare interpreter (pip only): proof-ledger installs nothing (C2)
#   PARITY_LOCK   optional lock directory taken (mkdir loop) for the whole run, for hosts where
#                 test runs that bind ports must not overlap
#   PARITY_IMAGE  tier 2's image: python:3.12 with the constrained install above (default
#                 trestle-ci-parity)
#   OUT           log directory (default: a fresh temp dir)
set -u
TIER=${1:?usage: ci_parity.sh 0|1|2 [WT] [BASE]}
WT=${2:-$PWD}; BASE=${3:-origin/master}
WT_PARENT=${WT:A:h}
PY=${PARITY_PY:-$WT_PARENT/.xdist-venv/bin/python}
NOINSTALL_PY=${NOINSTALL_PY:-$WT_PARENT/.ci-sim-venv/bin/python}
OUT=${OUT:-$(mktemp -d -t ci-parity)}; mkdir -p $OUT
for interp in $PY $NOINSTALL_PY; do
  [ -x $interp ] || { echo "ci_parity: no interpreter at $interp (set PARITY_PY / NOINSTALL_PY)"; exit 2; }
done
if [ -n "${PARITY_LOCK:-}" ]; then
  until mkdir $PARITY_LOCK 2>/dev/null; do sleep 2; done
  echo $$ > $PARITY_LOCK/pid
  trap 'rm -f $PARITY_LOCK/pid; rmdir $PARITY_LOCK 2>/dev/null' EXIT
fi
HEAD_SHA=$(git -C $WT rev-parse HEAD)
BASE_SHA=$(git -C $WT rev-parse $BASE) || { echo "ci_parity: no $BASE in $WT"; exit 2; }

# A clone, not `git archive`: the court reads git (d2's reader, record admissibility, carriers);
# an archive without .git fails test_d2 spuriously. `--shared` borrows WT's objects (read only).
COPY=$OUT/tree
git clone -q --shared $WT $COPY && git -C $COPY checkout -q --detach $HEAD_SHA || exit 2
git -C $COPY fetch -q $WT '+refs/tags/*:refs/tags/*' '+refs/archive/*:refs/archive/*' 2>/dev/null
# CI's release-graft step (ci.yml): a root commit carrying `Release-Of: <sha>` is grafted onto the
# archived history it was squashed from, when their trees are equal.
for root in $(git -C $COPY rev-list --max-parents=0 HEAD); do
  of=$(git -C $COPY log -1 --format='%(trailers:key=Release-Of,valueonly)' $root | sed -n 1p | tr -d '[:space:]')
  [ -n "$of" ] || continue
  git -C $COPY cat-file -e "$of^{commit}" 2>/dev/null \
    || git -C $COPY fetch -q --no-tags "$(git -C $WT remote get-url origin)" '+refs/archive/*:refs/archive/*'
  if [ "$(git -C $COPY rev-parse "$of^{tree}" 2>/dev/null)" = "$(git -C $COPY rev-parse "$root^{tree}")" ]; then
    git -C $COPY replace -f --graft $root $of
  else
    echo "ci_parity: release graft $root -> $of not possible (archive missing or trees differ)"; exit 2
  fi
done
cd $COPY
export PYTHONPATH=$COPY:$COPY/packages/trestle-packs:$COPY/packages/trestle-env PYTHONDONTWRITEBYTECODE=1
CLEAN_PATH=$($PY -m tests.proof.guards clean-path | tail -1)
fail=0
step() {  # step NAME CMD...
  local name=$1; shift; local s=$(date +%s)
  "$@" > $OUT/$name.log 2>&1; local rc=$?
  printf '%-26s rc=%-3s %4ss  %s\n' $name $rc $(( $(date +%s) - s )) "$(tail -1 $OUT/$name.log)"
  [ $rc -eq 0 ] || fail=1
}
ci_env() {
  env -u GH_TOKEN -u GITHUB_TOKEN GH_CONFIG_DIR=$(mktemp -d) TRESTLE_PROOF_GATE=ci-test \
      GITHUB_EVENT_NAME=pull_request GITHUB_ACTIONS=true PATH=$CLEAN_PATH "$@"
}
verdict() { echo "tier $1: $([ $fail = 0 ] && echo PASS || echo FAIL)  logs $OUT"; exit $fail; }
LINT_PATHS=(trestle tests packages/trestle-packs packages/trestle-env conftest.py scripts/smoke_packs.py scripts/demo_pack_workflows.py)

# ---- tier 0 -------------------------------------------------------------------------------------
# tool drift (mypy 2.3.1 -> 2.4.0, ruff 0.16.9 -> 0.16.10 on 2026-10-01): this interpreter must hold
# CI's exact set, or a green here proves nothing about CI
step versions $PY -c '
import importlib.metadata as m, pathlib, re, sys
norm = lambda n: re.sub(r"[-_.]+", "-", n).lower()
want = {}
for line in pathlib.Path("constraints/ci.txt").read_text().splitlines():
    line = line.split("#")[0].strip()
    if line:
        n, v = line.split("=="); want[norm(n)] = v
have = {norm(d.metadata["Name"]): d.version for d in m.distributions()}
bad = [f"{n} {have[n]}!={v}" for n, v in want.items() if n in have and have[n] != v]
bad += [f"{n} absent" for n in ("mypy", "ruff", "pytest") if n not in have]
print("mismatch: " + ", ".join(bad) if bad else "versions match constraints/ci.txt"); sys.exit(1 if bad else 0)'
step ruff-check  $PY -m ruff check $LINT_PATHS
step ruff-format $PY -m ruff format --check $LINT_PATHS
step mypy        $PY -m mypy
step shard-map   $PY -m tests.proof.ci_shards check
# SA-05 (the tolerance names and the sleep-as-synchronisation ratchet) and every other drift rule
step drift       ci_env $PY -m pytest -q -p no:cacheprovider tests/proof/drift
[ $TIER -ge 1 ] || verdict 0

# ---- tier 1 -------------------------------------------------------------------------------------
# the goldens closure pin and G-E2 live in pins; proof/fence/planted hold the court's selftests
for shard in pins proof fence planted; do
  step shard-$shard ci_env sh -c "$PY -m pytest -q -p no:cacheprovider \$($PY -m tests.proof.ci_shards args $shard)"
done
# per-file isolation (a planted-repo kdoc test failed only after another test warmed a cache):
# every changed test file, and every test file importing a changed module, in a process of its own
changed=$(git diff --name-only $(git merge-base HEAD $BASE_SHA) HEAD -- '*.py')
mods=$(print -l ${(f)changed} | grep -v '^tests/' | sed 's/\.py$//; s|/__init__$||; s|/|.|g')
files=$( { print -l ${(f)changed} | grep -E '(^|/)test_[^/]*\.py$'
           for m in ${(f)mods}; do git grep -l -E "(from|import) ${m//./\\.}\b" -- 'tests/*test_*.py' 'packages/*/tests/*test_*.py'; done
         } | sort -u)
isolation() {
  local f n=0 bad=0
  for f in ${(f)files}; do
    [ -f $f ] || continue
    n=$((n + 1))
    ci_env $PY -m pytest -q -p no:cacheprovider $f || { echo "ISOLATION FAIL $f"; bad=$((bad + 1)); }
  done
  echo "isolation: $n files, $bad failed"; [ $bad -eq 0 ]
}
step isolation isolation
# proof-ledger installs nothing (C2): its report step, and its imports, under a pip-only
# interpreter, over the results the shards above wrote
step proof-ledger-noinstall sh -c "$NOINSTALL_PY -m tests.proof.meta report --json > /dev/null && $NOINSTALL_PY -c 'import tests.proof.meta'"
# the landing view: the court's selftests on HEAD merged into BASE, unauthenticated (after a
# landing, the newest carrier -- what the CI anchor reads -- is the merge's, not the branch's)
LAND=$OUT/land
if git -C $COPY worktree add -q --detach $LAND $BASE_SHA \
   && git -C $LAND -c user.name=parity -c user.email=parity@local merge -q --no-ff --no-edit $HEAD_SHA; then
  step landing-selftest sh -c "cd $LAND && PYTHONPATH=$LAND:$LAND/packages/trestle-packs:$LAND/packages/trestle-env \
     env -u GH_TOKEN -u GITHUB_TOKEN GH_CONFIG_DIR=$(mktemp -d) PATH=$CLEAN_PATH \
     $PY -m pytest -q -p no:cacheprovider tests/proof/selftest"
else
  echo "landing-merge              HEAD does not merge cleanly into $BASE"; fail=1
fi
git -C $COPY worktree remove --force $LAND 2>/dev/null
[ $TIER -ge 2 ] || verdict 1

# ---- tier 2 -------------------------------------------------------------------------------------
for shard in baseline core single tree-host tree misc packs env; do
  step shard-$shard ci_env sh -c "$PY -m pytest -q -p no:cacheprovider \$($PY -m tests.proof.ci_shards args $shard)"
done
# The drills' nested REG is where the races surfaced (3 workers on CI's 4-vCPU runner); a many-core
# Mac hides them. A Linux container held to 4 CPUs is the runner's shape. The clone borrows WT's
# objects through an absolute alternates path, mounted read-only at the same path.
IMAGE=${PARITY_IMAGE:-trestle-ci-parity}
OBJECTS=$(git -C $WT rev-parse --path-format=absolute --git-common-dir)/objects
docker image inspect $IMAGE > /dev/null 2>&1 || { echo "ci_parity: no docker image $IMAGE (see PARITY_IMAGE)"; exit 2; }
for part in 0 1 2 3; do
  step drill-$part docker run --rm --cpus 4 --memory 16g -v $COPY:/w -v $OBJECTS:$OBJECTS:ro -w /w \
       -e TRESTLE_CK_ISOLATION=1 -e GITHUB_EVENT_NAME=pull_request -e PART=$part -e PARTS=4 $IMAGE sh -c '
    ids=$(pytest tests/core/tooling/test_ck_drill.py --collect-only -q -p no:cacheprovider -k test_decline_patch_applies_and_isolates | sed "/^$/q" | grep "::" | awk -v n=$PARTS -v k=$PART "(NR-1)%n==k")
    [ -z "$ids" ] || pytest -q $ids'
done
verdict 2
