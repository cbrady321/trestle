"""A fake `docker` CLI for testing `docker_stack` and `stack_pipeline` without an engine.

It answers the commands those wrappers run (`info`, `compose version`, `compose ... config
--services`, `up`, `stop`, `down`, `logs`) with Compose's semantics, over a JSON state file:

    {"containers": {"<project>-<service>-1": "running" | "exited"},
     "networks": ["<project>_default"],
     "volumes": {"<project>_<volume>": "seeded"},
     "calls": ["up --detach keeper", "down", ...],
     "fail": ["broken"],          # services whose `up` fails, like an uncached image
     "engine_down": false}        # `info` cannot reach the engine

`down` removes containers and networks, and volumes only with `--volumes`; `stop` stops
containers and removes nothing. The compose file is read line by line (top-level `services:` and
`volumes:` keys, and a service's `profiles: [..]`), enough for test fixtures.

Two ways in:

- in process, as a `FakeContext` exec handler: `FakeDocker(state_path).handler`;
- as an executable, for a plugin published on a real host: `write_fake_docker(folder, state)`
  writes `folder/docker`, a script that runs this module with the state path.

Standard library only, like the other fakes here.
"""

from __future__ import annotations

import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any

STATE_ENV = "TRESTLE_FAKE_DOCKER_STATE"
_GLOBAL_WITH_VALUE = {"--host", "-H", "--context", "-c", "--config"}
_COMPOSE_WITH_VALUE = {
    "--project-name": "project",
    "-p": "project",
    "--file": "files",
    "-f": "files",
    "--profile": "profiles",
    "--env-file": "env_files",
    "--project-directory": "directory",
}
_UP_WITH_VALUE = {"--wait-timeout", "--timeout", "-t", "--pull"}


def empty_state() -> dict[str, Any]:
    return {"containers": {}, "networks": [], "volumes": {}, "calls": [], "fail": []}


def read_state(path: Path) -> dict[str, Any]:
    state = empty_state()
    if path.exists():
        state.update(json.loads(path.read_text(encoding="utf-8")))
    return state


def _parse_compose(files: list[str]) -> tuple[dict[str, list[str]], list[str]]:
    """service -> its profiles, and the named volumes, read from the compose files."""
    services: dict[str, list[str]] = {}
    volumes: list[str] = []
    for name in files:
        section = ""
        current = ""
        for raw in Path(name).read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].rstrip()
            if not line.strip():
                continue
            top = re.fullmatch(r"([A-Za-z_][\w-]*):.*", line)
            if top:
                section, current = top.group(1), ""
                continue
            key = re.fullmatch(r"  ([A-Za-z0-9_][\w.-]*):.*", line)
            if key:
                current = key.group(1)
                if section == "services":
                    services.setdefault(current, [])
                elif section == "volumes":
                    volumes.append(current)
                continue
            profiles = re.fullmatch(r"    profiles:\s*\[(.*)\]", line)
            if profiles and section == "services" and current:
                services[current] = [p.strip().strip("'\"") for p in profiles.group(1).split(",")]
    return services, volumes


class FakeDocker:
    """The fake CLI over `state_path`. `run(argv, cwd)` returns `(exit code, stdout, stderr)`."""

    def __init__(self, state_path: Path) -> None:
        self.state_path = Path(state_path)

    @property
    def state(self) -> dict[str, Any]:
        return read_state(self.state_path)

    @property
    def calls(self) -> list[str]:
        return list(self.state["calls"])

    def set(self, **fields: Any) -> None:
        """Update the state's top-level fields (for example `fail=["broken"]`)."""
        state = self.state
        state.update(fields)
        self._save(state)

    def _save(self, state: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")

    def handler(self, argv: list[str], **kwargs: Any) -> Any:
        """A `FakeContext` exec handler: runs `argv` on the fake and returns an `ExecResult`."""
        from trestle.plugin.surface import ExecResult

        code, out, err = self.run(argv, cwd=kwargs.get("cwd"))
        return ExecResult(
            argv=tuple(argv),
            returncode=code,
            timed_out=False,
            cancelled=False,
            duration_s=0.0,
            stdout=out,
            stderr=err,
            output_bytes=len(out.encode()) + len(err.encode()),
            truncated=False,
            artifact="",
        )

    def run(self, argv: list[str], *, cwd: Any = None) -> tuple[int, str, str]:
        args = list(argv[1:])
        while args and args[0] in _GLOBAL_WITH_VALUE:
            args = args[2:]
        state = self.state
        try:
            if not args:
                return 1, "", "fake docker: no command\n"
            if args[0] == "info":
                if state.get("engine_down"):
                    return (
                        1,
                        "",
                        "Cannot connect to the Docker daemon. Is the docker daemon running?\n",
                    )
                return 0, "29.0.0-fake\n", ""
            if args[0] != "compose":
                return 1, "", f"fake docker: unsupported command {args[0]!r}\n"
            return self._compose(state, args[1:], Path(cwd) if cwd else Path.cwd())
        finally:
            self._save(state)

    def _compose(self, state: dict[str, Any], args: list[str], cwd: Path) -> tuple[int, str, str]:
        opts: dict[str, Any] = {"project": None, "files": [], "profiles": [], "env_files": []}
        while args and args[0] in _COMPOSE_WITH_VALUE:
            key = _COMPOSE_WITH_VALUE[args[0]]
            if isinstance(opts.get(key), list):
                opts[key].append(args[1])
            else:
                opts[key] = args[1]
            args = args[2:]
        if not args:
            return 1, "", "fake docker compose: no subcommand\n"
        command, rest = args[0], args[1:]
        if command == "version":
            state["calls"].append("compose version")
            return 0, "2.30.0-fake\n", ""
        files = [str(f if Path(f).is_absolute() else cwd / f) for f in opts["files"]]
        if not files:
            files = [str(cwd / "docker-compose.yml")]
        missing = [f for f in files if not Path(f).is_file()]
        if missing:
            return 1, "", f"open {missing[0]}: no such file or directory\n"
        services, volumes = _parse_compose(files)
        active = [
            s
            for s, profiles in services.items()
            if not profiles or set(profiles) & set(opts["profiles"])
        ]
        project = opts["project"] or Path(files[0]).parent.name.lower()
        flags = [a for a in rest if a.startswith("-")]
        names: list[str] = []
        skip = False
        for arg in rest:
            if skip:
                skip = False
            elif arg in _UP_WITH_VALUE:
                skip = True
            elif not arg.startswith("-"):
                names.append(arg)
        state["calls"].append(" ".join([command, *rest]))
        unknown = [n for n in names if n not in services]
        if unknown:
            return 1, "", f"no such service: {unknown[0]}\n"
        if command == "config":
            return 0, "".join(f"{s}\n" for s in active), ""
        if command == "up":
            wanted = names or active
            failing = [s for s in wanted if s in state.get("fail", [])]
            if failing:
                return (
                    1,
                    "",
                    f"Error response from daemon: no such image for service {failing[0]}\n",
                )
            for service in wanted:
                state["containers"][f"{project}-{service}-1"] = "running"
            if f"{project}_default" not in state["networks"]:
                state["networks"].append(f"{project}_default")
            for volume in volumes:
                state["volumes"].setdefault(f"{project}_{volume}", "seeded")
            return 0, "", "".join(f"Container {project}-{s}-1 Started\n" for s in wanted)
        if command == "stop":
            for name in list(state["containers"]):
                service = name[len(project) + 1 : -2] if name.startswith(f"{project}-") else None
                if service is not None and (not names or service in names):
                    state["containers"][name] = "exited"
            return 0, "", ""
        if command == "down":
            prefix = f"{project}-"
            state["containers"] = {
                k: v for k, v in state["containers"].items() if not k.startswith(prefix)
            }
            state["networks"] = [n for n in state["networks"] if n != f"{project}_default"]
            if "--volumes" in flags or "-v" in flags:
                state["volumes"] = {
                    k: v for k, v in state["volumes"].items() if not k.startswith(f"{project}_")
                }
            return 0, "", ""
        if command == "logs":
            return 0, "".join(f"{s}-1  | {s} started\n" for s in names or active), ""
        return 1, "", f"fake docker compose: unsupported subcommand {command!r}\n"


def write_fake_docker(folder: Path, state_path: Path) -> Path:
    """Write `folder/docker`: an executable that runs this fake over `state_path`."""
    folder.mkdir(parents=True, exist_ok=True)
    packs_root = Path(__file__).resolve().parents[2]
    script = folder / "docker"
    script.write_text(
        "#!/bin/sh\n"
        f"{STATE_ENV}='{state_path}' PYTHONPATH='{packs_root}' "
        f"exec '{sys.executable}' -m trestle_packs.fakes.docker_cli \"$@\"\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return script


def main() -> int:
    code, out, err = FakeDocker(Path(os.environ[STATE_ENV])).run(sys.argv[:], cwd=os.getcwd())
    sys.stdout.write(out)
    sys.stderr.write(err)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
