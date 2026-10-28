"""SA-14 drift proof for Slice B (L.NW-2.1): the attribution keys and the release-descriptor rules
the host-docker gate's WR-PROOF-6 diff relies on.

1. Every B4 compose fixture labels each service, network and volume it creates
   `trestle.proof.fixture=<fixture id>` (CSC-10), names its images only by role
   (`${TRESTLE_IMAGE_<ROLE>}`, `pull_policy: never`; MC-B-10: no image literal), so nothing a
   fixture starts is unattributable.
2. Every run-created name in the container adapters and their fakes is a run-scoped selector
   `trwr-<root run_id>-<path>` (MC-B-01): the token `trwr` only ever appears as `trwr-`.
3. Descriptors use only V-10's three forms `InRunGroup`, `ArgvRelease`, `Durable` (MC-25), and every
   Docker `ArgvRelease` sets `remove_argv` (B3-C3).

Reading (recorded, not a plan gap): the plan gives L.NW-2.1 no dependence on the ports module
(L.SV-5.6): this file scans fixtures and the adapter sources statically (AST), never importing
`ports.py`, so it is built ahead of A-1's protocols and becomes non-vacuous as B's fixtures and
adapters land. Each rule is proved live by a planted violation.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[4]
FIXTURE_LABEL = "trestle.proof.fixture"
ALLOWED_FORMS = {"InRunGroup", "ArgvRelease", "Durable"}
IMAGE_BY_ROLE = re.compile(r"^\$\{TRESTLE_IMAGE_[A-Z0-9_]+\}$")

# B4 compose fixtures (plan: packages/trestle-env/tests/fixtures/*/compose.yaml,
# packages/trestle-packs/tests/container/fixtures/compose-*.yaml, tests/fixtures/**). The unedited
# legacy `minimal-compose` (P0's alpine live-compose fixture) is not a B4 fixture.
FIXTURE_GLOBS = (
    "packages/trestle-env/tests/fixtures/**/compose*.y*ml",
    "packages/trestle-env/tests/fixtures/**/docker-compose*.y*ml",
    "packages/trestle-packs/tests/container/fixtures/**/*.y*ml",
    "tests/fixtures/apps/**/compose*.y*ml",
    "tests/fixtures/apps/**/docker-compose*.y*ml",
    "tests/fixtures/ref-compose/**/*.y*ml",
)
# adapter sources whose descriptors and names the rules read
ADAPTER_SOURCE_GLOBS = (
    "packages/trestle-packs/trestle_packs/container/**/*.py",
    "packages/trestle-packs/trestle_packs/fakes/container.py",
    "packages/trestle-packs/trestle_packs/fakes/compose.py",
)


def _labels(block: object) -> dict[str, str]:
    if isinstance(block, dict):
        return {str(k): str(v) for k, v in block.items()}
    out: dict[str, str] = {}
    for item in block or []:
        key, _, value = str(item).partition("=")
        out[key] = value
    return out


def check_compose_fixture(text: str) -> list[str]:
    """Violations of the fixture rules in one compose document."""
    doc = yaml.safe_load(text) or {}
    problems: list[str] = []
    for section in ("services", "networks", "volumes"):
        for name, spec in (doc.get(section) or {}).items():
            spec = spec or {}
            if section != "services" and spec.get("external"):
                continue  # not created by the fixture
            if section == "services":
                labels = _labels(spec.get("labels"))
                image = spec.get("image")
                if image is not None and not IMAGE_BY_ROLE.match(str(image)):
                    problems.append(
                        f"service {name}: image {image!r} is not ${{TRESTLE_IMAGE_<ROLE>}}"
                    )
                if image is not None and spec.get("pull_policy") != "never":
                    problems.append(f"service {name}: pull_policy must be never")
            else:
                labels = _labels(spec.get("labels"))
            if not labels.get(FIXTURE_LABEL):
                problems.append(f"{section[:-1]} {name}: no {FIXTURE_LABEL}=<fixture id> label")
    return problems


def _callee(node: ast.Call) -> str:
    func = node.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def _is_form_name(name: str) -> bool:
    return bool(re.search(r"(Release|Descriptor)$", name)) or (
        "Durable" in name and name != "Durable"
    )


def check_adapter_source(text: str) -> list[str]:
    """Violations of the selector-name and descriptor-form rules in one adapter source."""
    tree = ast.parse(text)
    problems: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if re.search(r"trwr(?!-)", node.value):
                problems.append(f"line {node.lineno}: {node.value!r} is not a `trwr-` selector")
        if isinstance(node, ast.ClassDef) and _is_form_name(node.name):
            if node.name not in ALLOWED_FORMS:
                problems.append(f"line {node.lineno}: descriptor form {node.name} is not in V-10")
        if not isinstance(node, ast.Call):
            continue
        name = _callee(node)
        if _is_form_name(name) and name not in ALLOWED_FORMS:
            problems.append(f"line {node.lineno}: descriptor form {name} is not in V-10")
        if name == "ArgvRelease":
            if node.args:
                problems.append(f"line {node.lineno}: ArgvRelease takes keyword arguments only")
            kws = {kw.arg: kw.value for kw in node.keywords}
            if None in kws:  # **spec: cannot be read statically
                continue
            value = kws.get("remove_argv")
            if value is None or (isinstance(value, ast.Constant) and value.value is None):
                problems.append(f"line {node.lineno}: ArgvRelease without remove_argv (B3-C3)")
    return problems


def _files(globs: tuple[str, ...]) -> list[Path]:
    seen: dict[Path, None] = {}
    for pattern in globs:
        for path in sorted(ROOT.glob(pattern)):
            if path.is_file() and "minimal-compose" not in path.parts:
                seen[path] = None
    return list(seen)


@pytest.mark.parametrize("sa", ["SA-14"])
def test_every_b4_fixture_object_carries_the_fixture_label_and_names_images_by_role(sa: str):
    offenders = {}
    for path in _files(FIXTURE_GLOBS):
        problems = check_compose_fixture(path.read_text())
        if problems:
            offenders[str(path.relative_to(ROOT))] = problems
    assert offenders == {}


@pytest.mark.parametrize("sa", ["SA-14"])
def test_adapter_names_are_selectors_and_descriptors_use_the_three_forms(sa: str):
    offenders = {}
    for path in _files(ADAPTER_SOURCE_GLOBS):
        problems = check_adapter_source(path.read_text())
        if problems:
            offenders[str(path.relative_to(ROOT))] = problems
    assert offenders == {}


GOOD_FIXTURE = """
services:
  db:
    image: ${TRESTLE_IMAGE_POSTGRES}
    pull_policy: never
    labels:
      trestle.proof.fixture: ref-compose
  web:
    image: ${TRESTLE_IMAGE_HTTP_SUPPORT}
    pull_policy: never
    labels: ["trestle.proof.fixture=ref-compose", "other=1"]
networks:
  backend:
    labels: {trestle.proof.fixture: ref-compose}
  outside:
    external: true
volumes:
  data:
    labels:
      trestle.proof.fixture: ref-compose
"""


@pytest.mark.parametrize("sa", ["SA-14"])
def test_planted_fixture_violations_are_detected(sa: str):
    assert check_compose_fixture(GOOD_FIXTURE) == []
    # a planted unlabelled service
    bad = GOOD_FIXTURE.replace(
        "  web:\n    image: ${TRESTLE_IMAGE_HTTP_SUPPORT}\n    pull_policy: never\n"
        '    labels: ["trestle.proof.fixture=ref-compose", "other=1"]\n',
        "  web:\n    image: ${TRESTLE_IMAGE_HTTP_SUPPORT}\n    pull_policy: never\n",
    )
    assert check_compose_fixture(bad) == [f"service web: no {FIXTURE_LABEL}=<fixture id> label"]
    # a planted unlabelled network and volume
    bad = GOOD_FIXTURE.replace("    labels: {trestle.proof.fixture: ref-compose}\n", "    {}\n")
    assert any(p.startswith("network backend") for p in check_compose_fixture(bad))
    bad = GOOD_FIXTURE.replace(
        "  data:\n    labels:\n      trestle.proof.fixture: ref-compose\n", "  data:\n"
    )
    assert any(p.startswith("volume data") for p in check_compose_fixture(bad))
    # an image literal, and a fixture that may pull
    bad = GOOD_FIXTURE.replace("${TRESTLE_IMAGE_POSTGRES}", "postgres:16-alpine")
    assert any("postgres:16-alpine" in p for p in check_compose_fixture(bad))
    bad = GOOD_FIXTURE.replace("    pull_policy: never\n", "", 1)
    assert any("pull_policy" in p for p in check_compose_fixture(bad))


@pytest.mark.parametrize("sa", ["SA-14"])
def test_planted_adapter_violations_are_detected(sa: str):
    good = (
        "def create(run_id, path, docker, endpoint):\n"
        "    selector = f'trwr-{run_id}-{path}'\n"
        "    return ArgvRelease(executable=docker, observe_argv=[docker], stop_argv=[docker],\n"
        "                       remove_argv=[docker, 'rm', selector])\n"
        "def other(x):\n"
        "    return InRunGroup(x), Durable(x)\n"
    )
    assert check_adapter_source(good) == []
    # a Docker ArgvRelease with no remove_argv, and one that sets it to None
    no_remove = good.replace(",\n                       remove_argv=[docker, 'rm', selector]", "")
    assert check_adapter_source(no_remove) == ["line 3: ArgvRelease without remove_argv (B3-C3)"]
    none_remove = good.replace("remove_argv=[docker, 'rm', selector]", "remove_argv=None")
    assert check_adapter_source(none_remove) == ["line 3: ArgvRelease without remove_argv (B3-C3)"]
    # a fourth descriptor form, called or declared
    assert check_adapter_source("x = PidRelease(1)\n") == [
        "line 1: descriptor form PidRelease is not in V-10"
    ]
    assert check_adapter_source("class CgroupRelease: ...\n") == [
        "line 1: descriptor form CgroupRelease is not in V-10"
    ]
    # a create name that is not a `trwr-` selector
    assert check_adapter_source("name = f'trwr_{run_id}-{path}'\n") == [
        "line 1: 'trwr_' is not a `trwr-` selector"
    ]
    assert check_adapter_source("name = 'trwr' + run_id\n") != []
