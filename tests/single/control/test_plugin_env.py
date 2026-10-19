"""G1: `[plugins.env]` in config.toml is the operator's environment for plugin runs.

Under `trestle service` the server's own environment is exactly PATH, HOME and TRESTLE_HOME, so
the env pack's operator variables reach a plugin only through this table. Values come only from
config.toml: nothing in a request sets them."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from trestle.common.types import RunView
from trestle.server.config import load_config
from trestle.server.main import create_kernel

PACK_VARS = {
    "TRESTLE_DOCKER_PATH": "/opt/fake/bin/docker",
    "TRESTLE_IMAGE_POSTGRES": "postgres:16-fake",
    "TRESTLE_IMAGE_REDIS": "redis:7-fake",
    "TRESTLE_ENV_COMPOSE_FILE": "/srv/fake/compose.yaml",
}

ENV_PLUGIN = """
import os

from trestle.plugin import Context, trestle


@trestle
def show_env(ctx: Context, names: list[str]) -> dict[str, str | None]:
    return {name: os.environ.get(name) for name in names}
"""


def _config(home: Path, body: str) -> None:
    (home / "config.toml").write_text(body, encoding="utf-8")


def test_pack_variables_reach_a_run_under_a_service_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    (plugins / "show_env.py").write_text(ENV_PLUGIN, encoding="utf-8")
    lines = "\n".join(f'{k} = "{v}"' for k, v in PACK_VARS.items())
    _config(home, f"[plugins.env]\n{lines}\n")
    # the service unit's environment: none of the pack's variables
    for name in [n for n in os.environ if n.startswith("TRESTLE_") and n != "TRESTLE_HOME"]:
        monkeypatch.delenv(name)
    monkeypatch.setenv("TRESTLE_ENV_COMPOSE_FILE", "/from/the/server/shell.yaml")

    kernel = create_kernel(home=home, plugin_dirs=[plugins], skip_recovery=True)
    view = kernel.control.run(
        plugin="show_env", args={"names": [*PACK_VARS, "TRESTLE_HOME"]}, wait_ms=20_000
    )
    assert isinstance(view, RunView) and view.state == "succeeded", view
    got = view.summary
    assert {k: got[k] for k in PACK_VARS} == PACK_VARS  # the table wins over the server's own
    assert got["TRESTLE_HOME"] == "<home>"  # the run's own home, scrubbed in the summary


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ('[plugins]\nenv = "x"\n', "must be a table"),
        ('[plugins.env]\n"1BAD" = "x"\n', "not an environment name"),
        ("[plugins.env]\nTRESTLE_DOCKER_PATH = 3\n", "must be a string"),
        ('[plugins.env]\nTRESTLE_HOME = "/elsewhere"\n', "may not set TRESTLE_HOME"),
        ('[plugins.env]\nTRESTLE_RUN_SECRETS = "x"\n', "may not set TRESTLE_RUN_SECRETS"),
    ],
)
def test_a_malformed_table_stops_the_load(tmp_path: Path, body: str, message: str) -> None:
    _config(tmp_path, body)
    with pytest.raises(ValueError, match=message):
        load_config(tmp_path)


def test_no_table_sets_nothing(tmp_path: Path) -> None:
    assert load_config(tmp_path).plugin_env == ()
    _config(tmp_path, '[plugins]\npaths = ["x"]\n')
    assert load_config(tmp_path).plugin_env == ()
    _config(tmp_path, '[plugins.env]\nB = "2"\nA = "1"\n')
    assert load_config(tmp_path).plugin_env == (("A", "1"), ("B", "2"))
