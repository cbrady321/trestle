"""`trestle pack install | remove | upgrade` with fake host runners (P-4, P-5, P-7, P-11, P-13,
P-16)."""

from __future__ import annotations

from pathlib import Path

import pytest

import trestle
from trestle.packs import PackError
from trestle.packs.install import Installer
from trestle.packs.lock import lock_mtime, read_lock

from .conftest import FakeHost, make_pack


def test_bundled_install_copies_under_owner_and_locks(
    installer: Installer, pack_home: Path, bundled: Path, host: FakeHost
) -> None:
    make_pack(bundled, "docker", ["docker_stack"], folder="trestle-pack-docker", library=True)
    entry = installer.install("docker")
    copy = pack_home / "packs" / "trestle" / "docker"
    assert entry.owner == "trestle"
    assert entry.kind == "bundled"
    assert Path(entry.pack_dir) == copy
    assert (copy / "plugins" / "docker_stack.py").is_file()
    assert entry.version == "1.2.3"
    assert entry.label == "docker@1.2.3 (trestle)"
    # P-7: the library installs non-editable from the copy; bundled trestle* names never hit PyPI
    assert host.pip_calls == [["install", "--quiet", "--no-deps", "--force-reinstall", str(copy)]]
    assert [p.name for p in read_lock(pack_home)] == ["docker"]


def test_bundled_wrappers_only_carries_trestle_version(
    installer: Installer, bundled: Path, host: FakeHost
) -> None:
    make_pack(bundled, "pytest", ["pytest_run"], folder="trestle-pack-pytest")
    assert installer.install("pytest").version == trestle.__version__
    assert host.pip_calls == []


def test_bundled_owner_is_not_choosable(installer: Installer, bundled: Path) -> None:
    make_pack(bundled, "docker", ["docker_stack"], folder="trestle-pack-docker")
    with pytest.raises(PackError, match="always trestle"):
        installer.install("docker", owner="company")


def test_unknown_bundled_names_the_available(installer: Installer, bundled: Path) -> None:
    make_pack(bundled, "docker", ["docker_stack"], folder="trestle-pack-docker")
    with pytest.raises(PackError, match=r"available: docker"):
        installer.install("github")


def test_path_install_is_served_in_place_and_editable(
    installer: Installer, tmp_path: Path, host: FakeHost, pack_home: Path
) -> None:
    src = make_pack(tmp_path / "work", "mine", ["my_tool"], library=True)
    entry = installer.install(str(src), owner="company")
    assert entry.owner == "company"
    assert Path(entry.pack_dir) == src.resolve()
    assert not (pack_home / "packs" / "company").exists()
    assert host.pip_calls == [["install", "--quiet", "-e", str(src.resolve())]]


@pytest.mark.parametrize("owner", ["trestle", "local"])
def test_reserved_owners_refused(installer: Installer, tmp_path: Path, owner: str) -> None:
    src = make_pack(tmp_path, "mine", ["my_tool"])
    with pytest.raises(PackError, match=f"owner {owner}"):
        installer.install(str(src), owner=owner)


def test_lock_mtime_moves_on_every_install(
    installer: Installer, tmp_path: Path, pack_home: Path
) -> None:
    src = make_pack(tmp_path, "mine", ["my_tool"])
    installer.install(str(src))
    first = lock_mtime(pack_home)
    installer.install(str(src))  # reinstall from the same place
    assert lock_mtime(pack_home) != first
    assert len(read_lock(pack_home)) == 1


def test_missing_tool_refused(installer: Installer, tmp_path: Path) -> None:
    src = make_pack(tmp_path, "mine", ["my_tool"], extra='[requires]\ntools = ["gh", "git"]\n')
    with pytest.raises(PackError, match="needs gh on the service PATH"):
        installer.install(str(src))


def test_undeclared_collision_refused_naming_both(
    installer: Installer, tmp_path: Path, pack_home: Path
) -> None:
    installer.install(str(make_pack(tmp_path, "first", ["shared", "a"])))
    with pytest.raises(
        PackError, match=r"pack second and installed pack first@path \(user\).*'shared'"
    ):
        installer.install(str(make_pack(tmp_path, "second", ["shared", "b"])))
    assert [p.name for p in read_lock(pack_home)] == ["first"]


@pytest.mark.parametrize("declaration", ['overrides = ["shared"]\n', 'replaces = "first"\n'])
def test_declared_collision_allowed(installer: Installer, tmp_path: Path, declaration: str) -> None:
    installer.install(str(make_pack(tmp_path, "first", ["shared", "a"])))
    entry = installer.install(
        str(make_pack(tmp_path, "second", ["shared", "b"], extra_pack=declaration))
    )
    assert entry.plugins == ("b", "shared")


def test_same_name_from_elsewhere_needs_replace(
    installer: Installer, bundled: Path, tmp_path: Path, pack_home: Path
) -> None:
    make_pack(bundled, "stack", ["stack_pipeline"], folder="trestle-pack-stack")
    installer.install("stack")
    work = make_pack(tmp_path / "work", "stack", ["stack_pipeline"])
    with pytest.raises(PackError, match="--replace stack"):
        installer.install(str(work))
    entry = installer.install(str(work), replace_pack="stack")
    assert entry.kind == "path"
    assert [p.kind for p in read_lock(pack_home)] == ["path"]
    # the displaced bundled copy is gone; the working copy is untouched
    assert not (pack_home / "packs" / "trestle" / "stack").exists()
    assert (work / "pack.toml").is_file()


def test_replace_unknown_pack(installer: Installer, tmp_path: Path) -> None:
    with pytest.raises(PackError, match="no installed pack"):
        installer.install(str(make_pack(tmp_path, "x", ["x"])), replace_pack="nope")


def test_requires_packs_installs_bundled_first(
    installer: Installer, bundled: Path, pack_home: Path
) -> None:
    for name, plugin in (("docker", "docker_stack"), ("pytest", "pytest_run")):
        make_pack(bundled, name, [plugin], folder=f"trestle-pack-{name}")
    make_pack(
        bundled,
        "stack",
        ["stack_pipeline"],
        folder="trestle-pack-stack",
        extra='[requires]\npacks = ["docker", "pytest"]\n',
    )
    installer.install("stack")
    assert [p.name for p in read_lock(pack_home)] == ["docker", "pytest", "stack"]
    assert read_lock(pack_home)[2].requires_packs == ("docker", "pytest")


def test_requires_unknown_pack_refused(installer: Installer, tmp_path: Path) -> None:
    src = make_pack(tmp_path, "x", ["x"], extra='[requires]\npacks = ["nowhere"]\n')
    with pytest.raises(PackError, match="requires pack 'nowhere'"):
        installer.install(str(src))


def test_upgrade_waits_for_live_runs_of_that_pack_only(
    installer: Installer, tmp_path: Path, host: FakeHost
) -> None:
    src = make_pack(tmp_path, "mine", ["my_tool"])
    installer.install(str(src))
    host.live = {"r_1": "other_plugin"}
    installer.install(str(src))
    assert host.sleeps == 0  # an unrelated live run never holds an install up
    host.live = {"r_2": "my_tool", "r_3": "other_plugin"}
    installer.install(str(src))
    assert host.sleeps == 1


def test_no_wait_refuses_while_live(installer: Installer, tmp_path: Path, host: FakeHost) -> None:
    src = make_pack(tmp_path, "mine", ["my_tool"])
    installer.install(str(src))
    host.live = {"r_2": "my_tool"}
    installer.no_wait = True
    with pytest.raises(PackError, match=r"r_2 \(my_tool\)"):
        installer.install(str(src))


def test_remove(installer: Installer, bundled: Path, pack_home: Path, host: FakeHost) -> None:
    make_pack(bundled, "docker", ["docker_stack"], folder="trestle-pack-docker", library=True)
    installer.install("docker")
    installer.remove("docker")
    assert read_lock(pack_home) == []
    assert not (pack_home / "packs" / "trestle" / "docker").exists()
    assert host.pip_calls[-1] == ["uninstall", "--quiet", "-y", "pack-docker"]


def test_remove_refused_while_required(installer: Installer, bundled: Path) -> None:
    make_pack(bundled, "docker", ["docker_stack"], folder="trestle-pack-docker")
    make_pack(
        bundled,
        "stack",
        ["stack_pipeline"],
        folder="trestle-pack-stack",
        extra='[requires]\npacks = ["docker"]\n',
    )
    installer.install("stack")
    with pytest.raises(PackError, match="required by stack"):
        installer.remove("docker")


def test_upgrade_reinstalls_from_the_release(
    installer: Installer, bundled: Path, pack_home: Path
) -> None:
    src = make_pack(bundled, "docker", ["docker_stack"], folder="trestle-pack-docker")
    installer.install("docker")
    (src / "plugins" / "docker_logs.py").write_text(
        (src / "plugins" / "docker_stack.py").read_text().replace("docker_stack", "docker_logs")
    )
    installer.upgrade([])
    assert read_lock(pack_home)[0].plugins == ("docker_logs", "docker_stack")
    assert (pack_home / "packs" / "trestle" / "docker" / "plugins" / "docker_logs.py").is_file()
