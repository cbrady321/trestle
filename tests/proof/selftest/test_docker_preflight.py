"""Selftest for the host-docker preflight (DM-24; L.P0-0d.6). Recorder
fake `docker` only — never a real engine, never a pull."""

from __future__ import annotations

from tests.proof.host import docker_gate
from tests.proof.host.docker_gate import __main__ as docker_gate_main
from tests.proof.host.docker_gate import preflight as preflight_mod


class _Recorder:
    def __init__(self, returncode: int = 0):
        self.calls: list[tuple[list[str], dict]] = []
        self.returncode = returncode

    def __call__(self, args, env):
        self.calls.append((args, env))

        class R:
            returncode = self.returncode
            stdout = ""
            stderr = "boom"

        return R()


def test_cli_absent_records_precondition_unmet():
    record = preflight_mod.run_preflight(docker_path="", which=lambda _name: None)
    assert record["status"] == "PRECONDITION_UNMET"
    assert "docker CLI" in record["engine"]


def test_engine_unreachable_records_precondition_unmet():
    recorder = _Recorder(returncode=1)
    record = preflight_mod.run_preflight(docker_path="/usr/local/bin/docker", runner=recorder)
    assert record["status"] == "PRECONDITION_UNMET"
    assert "unreachable" in record["engine"]


def test_unpinned_image_records_precondition_unmet():
    recorder = _Recorder(returncode=0)
    record = preflight_mod.run_preflight(
        docker_path="/usr/local/bin/docker",
        runner=recorder,
        images={"alpine": {"ref": "alpine:3.20", "digest": ""}},
    )
    assert record["status"] == "PRECONDITION_UNMET"
    assert any("unpinned" in m for m in record["images"])


def test_engine_addressed_on_working_socket_not_default_context():
    recorder = _Recorder(returncode=0)
    preflight_mod.run_preflight(
        docker_path="/usr/local/bin/docker",
        runner=recorder,
        images={"alpine": {"ref": "alpine:3.20", "digest": "sha256:" + "a" * 64}},
    )
    for _args, env in recorder.calls:
        assert env["DOCKER_HOST"].endswith(".docker/run/docker.sock")
        assert env.get("DOCKER_CONTEXT", "") != "default"


def test_strict_not_built(capsys):
    assert preflight_mod.STRICT_BUILT is False
    rc = docker_gate_main.main(["strict"])
    assert rc == 2
    assert "L.NW-2.2" in capsys.readouterr().out


def test_run_not_built(capsys):
    rc = docker_gate_main.main(["run"])
    assert rc == 2
    assert "L.NW-2.10" in capsys.readouterr().out


def test_tm_p0_13_probe_exits_0_present():
    """TM-P0-13's probe imports the module and reads `STRICT_BUILT`
    (side-effect free: no docker call, no record)."""
    import subprocess
    import sys

    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from tests.proof.host.docker_gate import preflight as p; "
            "sys.exit(1 if getattr(p, 'STRICT_BUILT', False) else 0)",
        ],
        cwd=docker_gate.__file__.rsplit("/tests/", 1)[0],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
