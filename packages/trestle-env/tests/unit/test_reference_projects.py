"""The reference catalog's projects (L.RB-4.3; MC-B-09, B3-C14): `demo-py` pins Python and lists an
allowlisted task; `demo-jvm` pins a JDK and lists a Gradle-shaped task whose argv[0] is the tool
`java` (resolved to an absolute path by the toolchain, never the wrapper script), that runs the
wrapper main class from the project's wrapper jar with the daemon and the toolchain auto-download
off."""

from __future__ import annotations

import json
from typing import Any

import pytest

from trestle_env.catalog import REFERENCE_PATH, Catalog, CatalogError, load_reference

WRAPPER_MAIN = "org.gradle.wrapper.GradleWrapperMain"
AUTO_DOWNLOAD_OFF = "-Porg.gradle.java.installations.auto-download=false"


def test_demo_py_pins_python_and_lists_an_allowlisted_task() -> None:
    project = load_reference().project("demo-py")
    assert project is not None
    assert [(str(p.tool), str(p.version)) for p in project.pin] == [("python", "3.12")]
    task = load_reference().task("demo-py", "version")
    assert task is not None and [str(a) for a in task.argv] == ["python", "--version"]
    test_task = load_reference().task("demo-py", "pytest")
    assert test_task is not None
    assert [str(a) for a in test_task.argv] == ["python", "-m", "pytest", "-q", "./"]


def test_demo_jvm_pins_a_jdk_and_runs_the_wrapper_main_class_not_the_script() -> None:
    catalog = load_reference()
    project = catalog.project("demo-jvm")
    assert project is not None
    assert [(str(p.tool), str(p.version)) for p in project.pin] == [("java", "21")]
    task = catalog.task("demo-jvm", "build")
    assert task is not None
    argv = [str(a) for a in task.argv]
    assert argv[0] == "java"  # the JDK the toolchain resolves, never `gradlew`
    assert argv[1:4] == ["-classpath", "./gradle/wrapper/gradle-wrapper.jar", WRAPPER_MAIN]
    assert "--no-daemon" in argv and AUTO_DOWNLOAD_OFF in argv
    assert not any("gradlew" in a for a in argv)


def test_the_engine_remediation_pair_is_declared_with_its_budget() -> None:
    pair = load_reference().remedy("adapter.docker_engine_unreachable")
    assert pair is not None
    assert (str(pair.effect), pair.attempts, pair.total_s, pair.cooldown_s) == (
        "start_engine",
        2,
        60.0,
        5.0,
    )
    assert load_reference().remedy("adapter.other") is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(attempts=0),
        lambda p: p.update(attempts=True),
        lambda p: p.update(attempts=99),
        lambda p: p.update(total_s=0),
        lambda p: p.update(cooldown_s=999),
        lambda p: p.update(code="not-a-code"),
        lambda p: p.update(extra=1),
        lambda p: p.pop("effect"),
    ],
)
def test_a_remediation_pair_out_of_bounds_or_unclosed_is_refused(mutate: Any) -> None:
    data = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
    mutate(data["remediation"][0])
    with pytest.raises(CatalogError):
        Catalog.from_data(data)


def test_one_remedy_per_code() -> None:
    data = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
    data["remediation"].append(dict(data["remediation"][0], effect="other_effect"))
    with pytest.raises(CatalogError) as caught:
        Catalog.from_data(data)
    assert caught.value.reason == "duplicate"
