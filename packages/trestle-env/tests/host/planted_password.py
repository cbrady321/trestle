"""A port factory for `TRESTLE_ENV_PORTS`: the real binding with a WRONG Postgres password in the
readiness exec environment (L.RB-2.2, L.RB-0.4's case; KDD 2).

The published plugin runs in a child process that cannot import the test session's modules, so a
host case names this file by absolute path (`/abs/planted_password.py:wrong_password`). Everything
but the readiness environment is the reference binding on the operator's real docker: the
container is created with the right password, and the authenticated check offers another one, so
readiness can never pass."""

from __future__ import annotations

from collections.abc import Mapping

from trestle_env.plugins._bind import reference_ports

PLANTED = {"PGPASSWORD": "planted-wrong-password"}


def wrong_password(environ: Mapping[str, str]) -> Mapping[type, object]:
    return reference_ports(environ, readiness_environment=PLANTED, use_seam=False)
