"""The host records a pack plugin's pack identity and its configuration files' digests in the
run's provenance at admission (P-1, P-20)."""

from __future__ import annotations

from pathlib import Path

from trestle.common.fsutil import sha256_bytes
from trestle.common.types import RunView
from trestle.packs.install import Installer
from trestle.server.main import create_kernel

from .conftest import make_pack, plugin_source


def _provenance(kernel, plugin: str) -> dict[str, object]:  # type: ignore[no-untyped-def]
    view = kernel.control.run(plugin=plugin, args={}, wait_ms=10000)
    assert isinstance(view, RunView), view
    out = kernel.control.query("run_provenance", {"run_id": view.run_id})
    assert isinstance(out, dict)
    return out["items"][0]


def test_pack_and_config_digests_recorded(
    installer: Installer, tmp_path: Path, pack_home: Path
) -> None:
    policy = tmp_path / "policy.toml"
    policy.write_text("no_checks = 'fail'\n", encoding="utf-8")
    (pack_home / "config.toml").write_text(
        f'[plugins.env]\nTRESTLE_TEST_POLICY = "{policy}"\n', encoding="utf-8"
    )
    pack = make_pack(
        tmp_path,
        "github",
        ["gh_status"],
        extra='[config]\nfiles = ["TRESTLE_TEST_POLICY", "TRESTLE_TEST_UNSET"]\n',
    )
    installer.install(str(pack), owner="company")
    local = tmp_path / "local"
    local.mkdir()
    (local / "plain.py").write_text(plugin_source("plain"), encoding="utf-8")
    kernel = create_kernel(home=pack_home, plugin_dirs=[local], skip_recovery=True)

    row = _provenance(kernel, "gh_status")
    assert row["pack"] == {"name": "github", "version": "path", "owner": "company"}
    assert row["config"] == {
        "TRESTLE_TEST_POLICY": {
            "path": str(policy),
            "sha256": sha256_bytes(policy.read_bytes()),
        },
        "TRESTLE_TEST_UNSET": {"path": None, "sha256": None},
    }
    # a changed policy is a different provenance for the same snapshot
    policy.write_text("no_checks = 'pass'\n", encoding="utf-8")
    again = _provenance(kernel, "gh_status")
    assert again["snapshot_id"] == row["snapshot_id"]
    assert again["config"] != row["config"]

    plain = _provenance(kernel, "plain")
    assert plain["pack"] is None and plain["config"] is None
