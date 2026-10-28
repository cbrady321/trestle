"""Live checks of the host steps, gated like the docker node: skipped when the host tool is
missing. git: a local repository through `file://` (no network); pip: a throwaway venv."""

from __future__ import annotations

import io
import shutil
import subprocess
import sys
import venv
from pathlib import Path

import pytest

from trestle.packs import PackError
from trestle.packs.install import Installer, Runners, _run_git
from trestle.packs.lock import read_lock

from .conftest import FakeHost, make_pack

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git live check needs git")


def _git(cwd: Path, *argv: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *argv],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    make_pack(root, "acme_stack", ["acme_up"], folder="packs/acme_stack")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "one")
    _git(root, "tag", "v1.0.0")
    return root


def _installer(home: Path, host: FakeHost) -> Installer:
    runners = host.runners()
    return Installer(
        home=home,
        runners=Runners(
            git=_run_git, pip=runners.pip, which=runners.which, live_runs=runners.live_runs
        ),
        out=io.StringIO(),
    )


@needs_git
def test_git_install_at_tag_copies_one_commit(repo: Path, pack_home: Path, host: FakeHost) -> None:
    entry = _installer(pack_home, host).install(f"file://{repo}@v1.0.0#packs/acme_stack", owner="company")
    copy = pack_home / "packs" / "company" / "acme_stack"
    assert Path(entry.pack_dir) == copy
    assert (copy / "plugins" / "acme_up.py").is_file()
    assert not (copy / ".git").exists()
    assert entry.commit == _git(repo, "rev-parse", "v1.0.0^{commit}")
    assert entry.ref == "v1.0.0"
    assert read_lock(pack_home)[0].label == f"acme_stack@{entry.commit[:12]} (company)"


@needs_git
def test_git_install_at_commit(repo: Path, pack_home: Path, host: FakeHost) -> None:
    sha = _git(repo, "rev-parse", "HEAD")
    entry = _installer(pack_home, host).install(f"file://{repo}@{sha[:10]}#packs/acme_stack")
    assert entry.commit == sha


@needs_git
def test_git_branch_refused(repo: Path, pack_home: Path, host: FakeHost) -> None:
    with pytest.raises(PackError, match="is a branch"):
        _installer(pack_home, host).install(f"file://{repo}@main#packs/acme_stack")


def _pip_ready() -> bool:
    try:
        import hatchling  # noqa: F401
    except ImportError:
        return False
    return True


@pytest.mark.skipif(not _pip_ready(), reason="pip live check needs hatchling in this Python")
def test_pip_installs_the_library_non_editable_from_the_copy(tmp_path: Path) -> None:
    """The real pip step against a throwaway venv (never the service's own Python)."""
    env_dir = tmp_path / "venv"
    venv.EnvBuilder(with_pip=True).create(env_dir)
    python = env_dir / "bin" / "python"
    # the build backend (hatchling) from this Python, with no index: no network
    import hatchling

    site = subprocess.run(
        [str(python), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (Path(site) / "hatch_host.pth").write_text(str(Path(hatchling.__file__).parents[1]) + "\n")
    lib = make_pack(tmp_path / "src", "livepack", ["live_tool"], library=True)
    (lib / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n'
        '[project]\nname = "pack-livepack"\nversion = "1.2.3"\n'
        '[tool.hatch.build.targets.wheel]\nonly-include = ["livepack_mod.py"]\n',
        encoding="utf-8",
    )
    (lib / "livepack_mod.py").write_text("VALUE = 7\n", encoding="utf-8")

    def pip(argv: list[str]) -> None:
        proc = subprocess.run(
            [
                str(python),
                "-m",
                "pip",
                "--disable-pip-version-check",
                *argv,
                "--no-build-isolation",
                "--no-index",
            ],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr[-2000:]

    home = tmp_path / "home"
    home.mkdir()
    bundled = tmp_path / "rel"
    shutil.copytree(lib, bundled / "trestle-pack-livepack")
    installer = Installer(home=home, runners=Runners(pip=pip), out=io.StringIO(), bundled=bundled)
    installer.install("livepack")
    probe = subprocess.run(
        [str(python), "-c", "import livepack_mod, sys; print(livepack_mod.__file__)"],
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
    ).stdout.strip()
    assert "site-packages" in probe  # installed, not linked to the copy
    assert sys.prefix not in probe
