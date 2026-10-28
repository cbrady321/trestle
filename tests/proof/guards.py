"""Guards I (L.P0-0b.5; MC-13 guards, MC-P0-09).

Fail-closed checks a proof session should run before trusting anything
else it observes:

- `check_forbidden_path_binaries()`: refuses to proceed if `mise`,
  `process-compose`, `aws` or `gradle` resolves on `PATH` (GitHub runners
  ship `aws` and `gradle`; `mise`/`process-compose` are the toolchain this
  program deliberately does not depend on — WR-CON-2).
- `build_clean_path()` / the `clean-path` CLI: a shadow `PATH` that hides
  only those four names — everything else the runner already resolves
  (`python`, `git`, ...) still resolves — so CI can neutralize the runner
  without touching it (DM-25).
- `scan_for_model_client_imports()`: an AST scan of `trestle/**` plus
  `pyproject.toml`'s own dependency list, for an import of, or a
  dependency on, a model-client package (WR-CON-5).
- `check_import_origin()`: fails closed unless `trestle` resolves inside
  this rootdir and `trestle_packs` resolves inside
  `packages/trestle-packs/trestle_packs` or is file-for-file identical to
  it — a stale site-packages copy (seen on this host's miniforge 3.12,
  which silently shadows the checkout when `PYTHONPATH` is not set) must
  never pass for the real thing.

Guards II (L.P0-0b.6):

- `install_credential_audit_hook()`: a `sys.addaudithook` that fails
  closed (raises) when anything opens an AWS credential/config path — it
  never reads the file to check it, only its path (WR-CON-1). Installing
  it is process-global and permanent for the interpreter's life, so
  proof-selftests exercise it in a subprocess, never in-process.
- `scrub_subprocess_env()`: an environment mapping with every `AWS_*`
  variable (plus `AWS_SHARED_CREDENTIALS_FILE`/`AWS_CONFIG_FILE`)
  replaced by a sentinel path, and `DOCKER_CONFIG` pointed at a fresh
  empty directory.
- `build_docker_shim_dir()`: a directory holding a `docker` shim that
  refuses `docker pull` outright and rewrites `docker compose ... up`
  into `... up --pull never` before `exec`-ing the real `docker` this
  host resolves — built only when `docker` actually resolves on `PATH`.
"""

from __future__ import annotations

import argparse
import ast
import filecmp
import importlib.util
import os
import shutil
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKS_CANONICAL = ROOT / "packages" / "trestle-packs" / "trestle_packs"

FORBIDDEN_PATH_BINARIES = ("mise", "process-compose", "aws", "gradle")

MODEL_CLIENT_PACKAGES = (
    "anthropic",
    "openai",
    "google.generativeai",
    "google.genai",
    "mistralai",
    "cohere",
    "ollama",
    "langchain_openai",
)


class GuardFailure(RuntimeError):
    pass


# -- PATH probe -----------------------------------------------------------


def find_forbidden_path_binaries(path_env: str | None = None) -> list[str]:
    search_path = path_env if path_env is not None else os.environ.get("PATH", "")
    return [
        name for name in FORBIDDEN_PATH_BINARIES if shutil.which(name, path=search_path) is not None
    ]


def check_forbidden_path_binaries(path_env: str | None = None) -> None:
    """Exit 3, naming the binary, if any forbidden name resolves on PATH."""
    found = find_forbidden_path_binaries(path_env)
    if found:
        print(f"forbidden binary on PATH: {found[0]}", file=sys.stderr)
        raise SystemExit(3)


# -- clean-path -------------------------------------------------------------


def build_clean_path(path_env: str | None = None, *, shadow_root: Path | None = None) -> str:
    """A PATH with the same resolution as `path_env` except that none of
    `FORBIDDEN_PATH_BINARIES` resolves. Only directories that actually
    contain a forbidden name are touched: such a directory is replaced by
    a fresh one holding a symlink to every *other* entry it had, so
    anything else the runner could resolve there still resolves."""
    search_path = path_env if path_env is not None else os.environ.get("PATH", "")
    dirs = search_path.split(os.pathsep) if search_path else []
    root = (
        shadow_root
        if shadow_root is not None
        else Path(tempfile.mkdtemp(prefix="trestle-clean-path-"))
    )
    root.mkdir(parents=True, exist_ok=True)

    new_dirs: list[str] = []
    for index, raw in enumerate(dirs):
        dir_path = Path(raw) if raw else None
        if dir_path is None or not dir_path.is_dir():
            new_dirs.append(raw)
            continue
        try:
            entries = list(dir_path.iterdir())
        except OSError:
            new_dirs.append(raw)
            continue
        names = {entry.name for entry in entries}
        if not names & set(FORBIDDEN_PATH_BINARIES):
            new_dirs.append(raw)
            continue

        shadow_dir = root / f"d{index}"
        shadow_dir.mkdir(parents=True, exist_ok=True)
        for entry in entries:
            if entry.name in FORBIDDEN_PATH_BINARIES:
                continue
            link = shadow_dir / entry.name
            try:
                link.symlink_to(entry.resolve())
            except OSError:
                continue
        new_dirs.append(str(shadow_dir))
    return os.pathsep.join(new_dirs)


def _cli_clean_path(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="tests.proof.guards clean-path")
    parser.add_argument("--github-env", action="store_true")
    args = parser.parse_args(argv)

    shadow = build_clean_path()
    print(shadow)
    if args.github_env:
        github_env_path = os.environ.get("GITHUB_ENV")
        if not github_env_path:
            print("clean-path --github-env: $GITHUB_ENV is not set", file=sys.stderr)
            return 2
        with open(github_env_path, "a", encoding="utf-8") as fh:
            fh.write(f"PATH={shadow}\n")
    return 0


# -- no model-client dependency or import ------------------------------------


def _pep508_base_name(requirement: str) -> str:
    spec = requirement.strip()
    for sep in ("[", ">", "<", "=", "!", "~", " ", ";", "@"):
        idx = spec.find(sep)
        if idx != -1:
            spec = spec[:idx]
    return spec.strip().lower().replace("_", "-")


def declared_dependency_names() -> set[str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = data.get("project", {})
    names = {_pep508_base_name(dep) for dep in project.get("dependencies", [])}
    for group in project.get("optional-dependencies", {}).values():
        names.update(_pep508_base_name(dep) for dep in group)
    return names


def _iter_python_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


def find_model_client_imports(root: Path) -> list[str]:
    hits: list[str] = []
    top_names = {pkg.split(".")[0] for pkg in MODEL_CLIENT_PACKAGES}
    for path in _iter_python_files(root):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in top_names:
                        hits.append(f"{path}:{node.lineno}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] in top_names:
                    hits.append(f"{path}:{node.lineno}: from {node.module} import ...")
    return hits


def scan_for_model_client_imports() -> list[str]:
    problems: list[str] = []
    dep_names = declared_dependency_names()
    for pkg in MODEL_CLIENT_PACKAGES:
        base = pkg.split(".")[0].replace("_", "-")
        if base in dep_names:
            problems.append(f"pyproject.toml dependency: {pkg}")
    problems.extend(find_model_client_imports(ROOT / "trestle"))
    return problems


# -- import-origin guard ------------------------------------------------------


def _dirs_identical(a: Path, b: Path) -> bool:
    if not a.is_dir() or not b.is_dir():
        return False
    comparison = filecmp.dircmp(a, b, ignore=["__pycache__"])
    if comparison.left_only or comparison.right_only or comparison.funny_files:
        return False
    _, mismatch, errors = filecmp.cmpfiles(a, b, comparison.common_files, shallow=False)
    if mismatch or errors:
        return False
    return all(_dirs_identical(a / name, b / name) for name in comparison.common_dirs)


def check_import_origin() -> None:
    """Fail closed unless `trestle` resolves inside `ROOT` and
    `trestle_packs` resolves inside `PACKS_CANONICAL` or is file-for-file
    identical to it."""
    trestle_spec = importlib.util.find_spec("trestle")
    if trestle_spec is None or trestle_spec.origin is None:
        print("import-origin guard: trestle not found", file=sys.stderr)
        raise SystemExit(3)
    trestle_origin = Path(trestle_spec.origin).resolve()
    if ROOT != trestle_origin and ROOT not in trestle_origin.parents:
        print(
            f"import-origin guard: trestle resolves outside rootdir: {trestle_origin}",
            file=sys.stderr,
        )
        raise SystemExit(3)

    packs_spec = importlib.util.find_spec("trestle_packs")
    if packs_spec is None or not packs_spec.origin:
        return  # trestle_packs is optional; absent is not a guard failure
    packs_origin_dir = Path(packs_spec.origin).resolve().parent
    canonical = PACKS_CANONICAL.resolve()
    if canonical == packs_origin_dir or canonical in packs_origin_dir.parents:
        return
    if _dirs_identical(packs_origin_dir, canonical):
        return
    print(
        f"import-origin guard: trestle_packs resolves outside rootdir "
        f"and diverges: {packs_origin_dir}",
        file=sys.stderr,
    )
    raise SystemExit(3)


# -- credential-read audit hook ---------------------------------------------

_CREDENTIAL_PATH_MARKERS = (".aws/credentials", ".aws/config")


def _is_credential_path(path: object) -> bool:
    if path is None:
        return False
    if isinstance(path, bytes):
        try:
            path = path.decode("utf-8", "replace")
        except UnicodeDecodeError:
            return False
    text = os.fspath(path) if not isinstance(path, str) else path
    normalized = text.replace("\\", "/")
    return any(marker in normalized for marker in _CREDENTIAL_PATH_MARKERS)


def _credential_audit_hook(event: str, args: tuple[object, ...]) -> None:
    if event not in ("open", "io.open"):
        return
    path = args[0] if args else None
    if _is_credential_path(path):
        raise PermissionError(
            f"trestle credential-read guard: refusing to open {path!r} (WR-CON-1)"
        )


def install_credential_audit_hook() -> None:
    """Fail closed on any attempt to open an AWS credential/config path.
    Global and permanent for this interpreter's life (`sys.addaudithook`
    cannot be removed) — call this only in a subprocess meant to carry it
    for its whole run, never inside a long-lived test process."""
    sys.addaudithook(_credential_audit_hook)


# -- subprocess env scrub -----------------------------------------------------

ENV_SENTINEL = "/nonexistent/trestle-guard-sentinel"


def scrub_subprocess_env(base_env: dict[str, str] | None = None) -> dict[str, str]:
    """An environment mapping with every credential-bearing variable
    replaced: `AWS_*` (including the two credential/config file path
    variables) point at a path that does not exist, and `DOCKER_CONFIG`
    points at a fresh empty directory (never the real one, which can hold
    registry auth)."""
    env = dict(base_env if base_env is not None else os.environ)
    for key in list(env):
        if key.startswith("AWS_"):
            env[key] = ENV_SENTINEL
    env["AWS_SHARED_CREDENTIALS_FILE"] = ENV_SENTINEL
    env["AWS_CONFIG_FILE"] = ENV_SENTINEL
    env["DOCKER_CONFIG"] = tempfile.mkdtemp(prefix="trestle-empty-docker-config-")
    return env


# -- docker no-pull shim -------------------------------------------------------

_DOCKER_SHIM_TEMPLATE = '''#!/usr/bin/env python3
"""Trestle docker no-pull shim (L.P0-0b.6; WR-PROOF-5:no-pull). Refuses
`docker pull` outright; rewrites `docker compose ... up` to add
`--pull never`; anything else execs straight through to the real docker
this shim was built against."""
import os
import sys

REAL_DOCKER = {real_docker!r}
argv = sys.argv[1:]

if argv[:1] == ["pull"]:
    sys.stderr.write("trestle docker shim: image pull refused (WR-PROOF-5:no-pull)\\n")
    raise SystemExit(1)

if "compose" in argv and "up" in argv and "--pull" not in argv:
    compose_idx = argv.index("compose")
    up_idx = argv.index("up", compose_idx)
    argv = argv[: up_idx + 1] + ["--pull", "never"] + argv[up_idx + 1 :]

os.execv(REAL_DOCKER, [REAL_DOCKER, *argv])
'''


def build_docker_shim_dir(
    *,
    real_docker: str | None = None,
    shim_root: Path | None = None,
    path_env: str | None = None,
) -> Path | None:
    """A directory whose `docker` is the no-pull shim wrapping whichever
    `docker` actually resolves on `PATH` — or `None` if none does (there is
    nothing to guard). `real_docker` overrides auto-detection outright
    (including with `None`, meaning "resolve nothing" — pass `path_env`
    instead to control what auto-detection searches)."""
    if real_docker is not None:
        resolved: str | None = real_docker
    else:
        resolved = shutil.which(
            "docker", path=path_env if path_env is not None else os.environ.get("PATH", "")
        )
    if resolved is None:
        return None
    root = (
        shim_root
        if shim_root is not None
        else Path(tempfile.mkdtemp(prefix="trestle-docker-shim-"))
    )
    root.mkdir(parents=True, exist_ok=True)
    shim_path = root / "docker"
    shim_path.write_text(_DOCKER_SHIM_TEMPLATE.format(real_docker=resolved), encoding="utf-8")
    shim_path.chmod(0o755)
    return root


# -- CLI ----------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "clean-path":
        return _cli_clean_path(argv[1:])
    print("usage: python -m tests.proof.guards clean-path [--github-env]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
