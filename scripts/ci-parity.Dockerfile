# trestle-ci-parity: the image `scripts/ci_parity.sh` tier 2 runs the CK drill parts in
# (PARITY_IMAGE, default `trestle-ci-parity`). It reproduces the CI runner's shape: Linux,
# CPython 3.12, CI's exact third-party set (constraints/ci.txt, as CI's PIP_CONSTRAINT), a
# non-root user. ci_parity.sh adds the 4-CPU / 16 GB limits at `docker run`.
#
# The project is NOT left installed: ci_parity.sh mounts its clone of the checked head at /w, and
# PYTHONPATH below makes that clone the code under test (an installed copy could shadow it). The
# image holds only the dependencies, so it is rebuilt when constraints/ci.txt or a dependency list
# in a pyproject.toml changes, not per head.
#
# Build, from the repository root (never built by an agent; needs Docker Desktop and the user's
# go-ahead; status: written, not yet built or run):
#   docker build -f scripts/ci-parity.Dockerfile -t trestle-ci-parity .
# Run: scripts/ci_parity.sh 2 [WT] [BASE]
FROM python:3.12-bookworm

# procps: the proof court reads the process table (`ps`); git ships with the base image and reads
# the mounted clone, whose files belong to the host's user.
RUN apt-get update \
 && apt-get install -y --no-install-recommends procps \
 && rm -rf /var/lib/apt/lists/* \
 && git config --system --add safe.directory '*'

WORKDIR /src
COPY . /src
RUN pip install --no-cache-dir -c constraints/ci.txt ".[dev,packs,env]" \
 && pip uninstall -y trestle trestle-packs trestle-env \
 && rm -rf /src

# GitHub's hosted runner runs jobs as an unprivileged user (uid 1001): tests that make a file
# unreadable must not pass merely because root can read anything.
RUN useradd --create-home --uid 1001 runner
USER runner
ENV PYTHONPATH=/w:/w/packages/trestle-packs:/w/packages/trestle-env \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /w
