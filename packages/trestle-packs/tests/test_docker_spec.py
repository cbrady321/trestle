"""Tests for StackSpec parsing."""

from __future__ import annotations

from trestle_packs.docker.spec import StackSpec, WaitMode


def test_stack_spec_from_dict() -> None:
    spec = StackSpec.from_dict(
        {
            "compose_file": "compose.yml",
            "project": "demo",
            "waves": [
                {
                    "name": "data",
                    "services": ["postgres", "redis"],
                    "wait": "healthy",
                    "timeout_s": 60,
                },
                {"name": "app", "services": ["api"], "wait": "started"},
            ],
            "probes": {
                "postgres": {"kind": "command", "command": ["pg_isready"]},
            },
        }
    )
    assert spec.project == "demo"
    assert spec.compose_file == "compose.yml"
    assert spec.all_services() == ["postgres", "redis", "api"]
    assert spec.waves[0].wait == WaitMode.HEALTHY
    assert spec.probes["postgres"].command == ["pg_isready"]
