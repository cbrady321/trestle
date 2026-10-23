"""pack.toml and install-spec parsing (P-4, P-11, P-16, P-20)."""

from __future__ import annotations

from pathlib import Path

import pytest

from trestle.packs import PackError
from trestle.packs.install import parse_spec
from trestle.packs.lock import KIND_BUNDLED, KIND_GIT, KIND_PATH
from trestle.packs.manifest import load_manifest, wrapper_names

from .conftest import make_pack


def test_full_manifest(tmp_path: Path) -> None:
    pack = make_pack(
        tmp_path,
        "acme_github",
        ["github_pr_open"],
        extra_pack='replaces = "github"\noverrides = ["github_pr_open"]\n',
        extra=(
            '[requires]\ntools = ["gh"]\npacks = ["docker"]\n'
            '[config]\nfiles = ["TRESTLE_GITHUB_POLICY"]\n'
        ),
    )
    manifest = load_manifest(pack)
    assert manifest.name == "acme_github"
    assert manifest.replaces == "github"
    assert manifest.overrides == ("github_pr_open",)
    assert manifest.tools == ("gh",)
    assert manifest.packs == ("docker",)
    assert manifest.config_files == ("TRESTLE_GITHUB_POLICY",)


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ('[pack]\nname = "Bad"\n', "must match"),
        ('[pack]\nname = "ok"\nversion = "1"\n', "unknown key 'version'"),
        ('[pack]\nname = "ok"\n[hooks]\nx = 1\n', "unknown table [hooks]"),
        ('[pack]\nname = "ok"\nreplaces = "ok"\n', "the pack itself"),
        ('[pack]\nname = "ok"\nlibrary = "../x"\n', "inside the pack"),
        ('[pack]\nname = "ok"\n[config]\nfiles = ["lower"]\n', "not a valid name"),
    ],
)
def test_manifest_refusals(tmp_path: Path, body: str, fragment: str) -> None:
    (tmp_path / "pack.toml").write_text(body, encoding="utf-8")
    with pytest.raises(PackError, match=fragment.replace("[", r"\[").replace("]", r"\]")):
        load_manifest(tmp_path)


def test_not_a_pack(tmp_path: Path) -> None:
    with pytest.raises(PackError, match="has no pack.toml"):
        load_manifest(tmp_path)


def test_wrapper_names_read_from_source(tmp_path: Path) -> None:
    pack = make_pack(
        tmp_path,
        "p",
        {
            "file_a": (
                "from trestle.plugin.surface import trestle\n\n"
                "@trestle\ndef named_b(ctx):\n    return {}\n"
            )
        },
    )
    (pack / "plugins" / "_helper.py").write_text("x = 1\n", encoding="utf-8")
    assert set(wrapper_names(pack / "plugins")) == {"named_b"}


def test_parse_spec_kinds(tmp_path: Path) -> None:
    assert parse_spec("stack").kind == KIND_BUNDLED
    assert parse_spec(str(tmp_path)).kind == KIND_PATH
    git = parse_spec("git@github.com:acme/trestle-packs.git@v1.4.0#packs/bdc")
    assert (git.kind, git.location, git.ref, git.subdir) == (
        KIND_GIT,
        "git@github.com:acme/trestle-packs.git",
        "v1.4.0",
        "packs/bdc",
    )
    assert parse_spec("https://example.com/a/b.git@abc1234").ref == "abc1234"


@pytest.mark.parametrize(
    "spec",
    ["git@github.com:acme/x.git", "https://example.com/a/b.git", "file:///tmp/x@v1#../up"],
)
def test_git_spec_needs_a_ref(spec: str) -> None:
    with pytest.raises(PackError):
        parse_spec(spec)


def test_unknown_spec(tmp_path: Path) -> None:
    with pytest.raises(PackError, match="not a bundled pack name"):
        parse_spec(str(tmp_path / "missing" / "x"))
