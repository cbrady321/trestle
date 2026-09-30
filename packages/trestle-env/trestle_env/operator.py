"""The reference operator configuration, read as data (L.RB-0.3; WR-CANCEL-4, F-11(b), B2-C9).

An operator configuration is a `config.toml` in a Trestle home. The reference one
(`tests/fixtures/operator/config.toml`) names two things: the directory the reference plugin is
published from (`[plugins] paths`, the convention of `trestle/server/plugin_paths.py`) and the
executables the host sweep may run for an `ArgvRelease` (`[operator] release_executables`, V-10.1).

The library ships that allowlist EMPTY (`OperatorLimits.release_executables`, F-11(b) neutral,
TM-B2-2): a descriptor whose executable is not listed is disposed `unknown` and never run. Only
this configuration lists the operator's resolved absolute docker path, so a reference run's docker
release is run by the sweep and no other run's is. `trestle_env` may not import the server, so this
module returns plain values; the caller builds `OperatorLimits(release_executables=...)` from them.

The two placeholders are filled from the caller's values, never from the environment or `PATH`:
the docker path must already be absolute (a relative or empty one is refused, never resolved
against a working directory).
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

DOCKER_PLACEHOLDER = "@DOCKER@"
PLUGIN_DIR_PLACEHOLDER = "@PLUGIN_DIR@"


class OperatorConfigError(ValueError):
    """The configuration cannot be read as the reference operator configuration."""


@dataclass(frozen=True)
class OperatorConfig:
    plugin_dirs: tuple[Path, ...]
    release_executables: frozenset[str]


def render_operator_config(template: Path, *, docker: str, plugin_dir: Path) -> str:
    """The configuration text for a Trestle home: `template` with the resolved values filled in."""
    if not docker or not os.path.isabs(docker):
        raise OperatorConfigError(f"the docker path must be absolute, got {docker!r}")
    if not plugin_dir.is_absolute():
        raise OperatorConfigError(f"the plugin directory must be absolute, got {str(plugin_dir)!r}")
    text = template.read_text(encoding="utf-8")
    for placeholder in (DOCKER_PLACEHOLDER, PLUGIN_DIR_PLACEHOLDER):
        if placeholder not in text:
            raise OperatorConfigError(f"the template has no {placeholder}")
    return text.replace(DOCKER_PLACEHOLDER, docker).replace(PLUGIN_DIR_PLACEHOLDER, str(plugin_dir))


def load_operator_config(template: Path, *, docker: str, plugin_dir: Path) -> OperatorConfig:
    raw = tomllib.loads(render_operator_config(template, docker=docker, plugin_dir=plugin_dir))
    plugins = raw.get("plugins")
    operator = raw.get("operator")
    if not isinstance(plugins, dict) or not isinstance(operator, dict):
        raise OperatorConfigError("the configuration needs [plugins] and [operator] tables")
    paths = plugins.get("paths")
    executables = operator.get("release_executables")
    if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
        raise OperatorConfigError("[plugins] paths must be a list of strings")
    if not isinstance(executables, list) or not all(isinstance(e, str) for e in executables):
        raise OperatorConfigError("[operator] release_executables must be a list of strings")
    return OperatorConfig(tuple(Path(p) for p in paths), frozenset(executables))
