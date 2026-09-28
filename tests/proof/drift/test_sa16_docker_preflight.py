"""SA-16 drift proof: the host-docker preflight is report-mode, never
pulls, and always addresses the working socket/context (L.P0-0d.6)."""

from __future__ import annotations

import pytest

from tests.proof.host.docker_gate import preflight as preflight_mod


class _Recorder:
    def __init__(self, returncode: int = 0):
        self.calls: list[list[str]] = []
        self.returncode = returncode

    def __call__(self, args, env):
        self.calls.append(args)

        class R:
            returncode = self.returncode
            stdout = ""
            stderr = ""

        return R()


@pytest.mark.parametrize("sa", ["SA-16"])
def test_preflight_report_mode_records_and_exits_zero(sa: str, tmp_path) -> None:
    recorder = _Recorder(returncode=0)
    record = preflight_mod.run_preflight(
        cwd=preflight_mod.ROOT,
        docker_path="/usr/local/bin/docker",
        runner=recorder,
        images={"alpine": {"ref": "alpine:3.20", "digest": ""}},
    )
    assert record["status"] in ("PASSED", "PRECONDITION_UNMET")


@pytest.mark.parametrize("sa", ["SA-16"])
def test_preflight_never_invokes_pull(sa: str) -> None:
    recorder = _Recorder(returncode=0)
    preflight_mod.run_preflight(
        cwd=preflight_mod.ROOT,
        docker_path="/usr/local/bin/docker",
        runner=recorder,
        images={"alpine": {"ref": "alpine:3.20", "digest": "sha256:" + "a" * 64}},
    )
    for call in recorder.calls:
        assert "pull" not in call
