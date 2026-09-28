"""The live record check every checkpoint's role 2 cites (CM-5/CM-6).
Named neither `test_*.py` nor `*_test.py`, so it is never default-collected
(DM-80); a checkpoint names this path explicitly.
"""

from __future__ import annotations

from pathlib import Path

from tests.proof import meta as meta_mod
from tests.proof.host import record as record_mod

ROOT = Path(__file__).resolve().parents[3]


def test_committed_records_valid_for_head() -> None:
    """`select("host-proc", HEAD)` exists, is schema-valid, comes from one
    default-set run and has no pass-set violation. A host-docker record
    is checked (schema-valid; a `run` record has no pass-set violation, a
    `preflight` record passes with any status) only when `paired_docker`
    returns one."""
    head = record_mod.fence_mod._git(ROOT, "rev-parse", "HEAD").stdout.strip()  # noqa: SLF001
    proc_record = record_mod.select("host-proc", head, cwd=ROOT)
    assert proc_record is not None, "no admissible host-proc record for HEAD"
    record_mod.validate_schema(proc_record)
    labels = {lbl["id"]: lbl for lbl in meta_mod._load_all_labels()}  # noqa: SLF001
    assert record_mod.pass_set_violations(proc_record, labels) == []

    docker_record = record_mod.paired_docker(proc_record, cwd=ROOT)
    if docker_record is not None:
        record_mod.validate_schema(docker_record)
        if docker_record["mode"] == "run":
            assert record_mod.pass_set_violations(docker_record, labels) == []
