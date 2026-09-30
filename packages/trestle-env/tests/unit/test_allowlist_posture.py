"""The release-executable allowlist posture: empty by default, populated only by the reference
operator configuration (L.RB-0.3; WR-CANCEL-4, F-11(b), TM-B2-2)."""

from __future__ import annotations

from pathlib import Path

import pytest
from trestle.server.config import OperatorLimits, TrestleConfig, load_config
from trestle.server.plugin_paths import resolve_plugin_dirs

from trestle_env import operator

HERE = Path(__file__).resolve().parents[1]
REFERENCE_CONFIG = HERE / "fixtures" / "operator" / "config.toml"
REFERENCE_PLUGINS = Path(operator.__file__).resolve().parent / "plugins"


def resolved_docker(tmp_path: Path) -> str:
    """A stand-in for the operator's resolved docker path: absolute, never searched for."""
    docker = tmp_path / "bin" / "docker"
    docker.parent.mkdir()
    docker.write_text("#!/bin/sh\n", encoding="utf-8")
    docker.chmod(0o755)
    return str(docker)


@pytest.mark.proves(
    "WR-CANCEL-4",
    "WR-CANCEL-4:b-allowlist-reference-populated-default-empty",
    "B",
    "B",
    "LOGIC",
    "CI",
)
def test_default_allowlist_empty_and_reference_config_populated(tmp_path: Path) -> None:
    # the library default is empty, whatever a home's config.toml lists (F-11(b) neutral)
    assert TrestleConfig.defaults().operator_limits.release_executables == frozenset()
    docker = resolved_docker(tmp_path)
    config = operator.load_operator_config(
        REFERENCE_CONFIG, docker=docker, plugin_dir=REFERENCE_PLUGINS
    )
    assert config.release_executables == frozenset({docker})
    assert config.plugin_dirs == (REFERENCE_PLUGINS,)
    # the reference configuration is what builds the reference server's limits; nothing else does
    limits = OperatorLimits(release_executables=config.release_executables)
    assert limits.release_executables == {docker}
    assert TrestleConfig(operator_limits=limits).operator_limits.release_executables == {docker}
    assert OperatorLimits().release_executables == frozenset()
    # the rendered text is a home's config.toml: the server reads its plugin path from it, and
    # reading it does not put an executable in the default allowlist
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text(
        operator.render_operator_config(
            REFERENCE_CONFIG, docker=docker, plugin_dir=REFERENCE_PLUGINS
        ),
        encoding="utf-8",
    )
    assert resolve_plugin_dirs(home) == [REFERENCE_PLUGINS.resolve()]
    assert load_config(home).operator_limits.release_executables == frozenset()


@pytest.mark.parametrize("docker", ["", "docker", "bin/docker", "./docker"])
def test_a_docker_path_that_is_not_absolute_is_refused_never_resolved(docker: str) -> None:
    with pytest.raises(operator.OperatorConfigError):
        operator.load_operator_config(REFERENCE_CONFIG, docker=docker, plugin_dir=REFERENCE_PLUGINS)


def test_the_reference_plugin_directory_holds_the_reference_plugin() -> None:
    plugins = sorted(p.name for p in REFERENCE_PLUGINS.glob("*.py") if not p.name.startswith("_"))
    assert plugins == ["reference_env.py"]
