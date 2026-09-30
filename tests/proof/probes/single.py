"""Presence probes of the SINGLE phase's temporary mechanisms (CSC-5; CM-7 probe form).

    python -m tests.proof.probes.single multi-vertex-refusal [--phase full | choice-only]
    python -m tests.proof.probes.single one-vertex-spine-fixture

Exit 0 = present (in the named phase), exit 1 = absent, exit 2 = a usage error. A probe decides
presence by exit status alone: it is side-effect free (a throwaway home under a temporary
directory, removed on exit; no run is started; nothing in the repository or the operator's home is
written) and never calls `meta register` (a recursive probe is refused, CM-7).

`multi-vertex-refusal` (TM-B2-1, L.SV-3.5) asks the real `Admission.admit` about the two composite
fixtures (`probe_all_root`, an `AllDeclaration` root; `probe_choice_root`, a `ChoiceNode` root):

* `--phase full`: both are refused `admission.plan_multi_vertex_unsupported`;
* `--phase choice-only`: the `ChoiceNode` root is refused and the `AllDeclaration` root is not
  (L.TR-L.1 lifts the refusal for it);
* no phase: the refusal is present in some phase, i.e. the `ChoiceNode` root is refused (until
  L.TR-5.3 removes the entry).

`one-vertex-spine-fixture` (TM-B2-3, L.SV-5.9) is present iff the spine gate's parametrization
(`SPINE_FIXTURES` in `tests/single/spine/test_w_a1.py`, read as a literal: no import, nothing run)
is exactly `{spine_leaf}` and that fixture file exists.

A fixture that cannot be admitted for another reason is not "refused for shape": the probe counts
only the temporary code.
"""

from __future__ import annotations

import argparse
import ast
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
FIXTURES = REPO / "tests" / "fixtures" / "workflows"
ALL_ROOT = "probe_all_root"
CHOICE_ROOT = "probe_choice_root"
PHASES = ("full", "choice-only")
SPINE_GATE = REPO / "tests" / "single" / "spine" / "test_w_a1.py"
SPINE_FIXTURE = "spine_leaf"


def refused_for_shape(plugin_sources: dict[str, str]) -> dict[str, bool]:
    """For each plugin (name -> source), whether `Admission.admit` refuses it with the
    multi-vertex code, in a throwaway home that is removed before returning."""
    # the publication validator runs plugin code in a child interpreter: it must import this
    # checkout's `trestle`, not whichever tree an editable install points at
    os.environ["PYTHONPATH"] = os.pathsep.join(
        [str(REPO), *filter(None, [os.environ.get("PYTHONPATH")])]
    )
    from trestle.common import codes
    from trestle.common.types import AdmitRequest, AdmitResultRefused
    from trestle.server.main import create_kernel

    tmp = Path(tempfile.mkdtemp(prefix="trestle-probe-"))
    try:
        plugin_dir = tmp / "plugins"
        plugin_dir.mkdir()
        for name, source in plugin_sources.items():
            (plugin_dir / f"{name}.py").write_text(source, encoding="utf-8")
        kernel = create_kernel(home=tmp / "home", plugin_dirs=[plugin_dir], skip_recovery=True)
        verdicts: dict[str, bool] = {}
        for name in plugin_sources:
            result = kernel.control.admission.admit(AdmitRequest(plugin=name, args={}))
            verdicts[name] = (
                isinstance(result, AdmitResultRefused)
                and result.outcome.code == codes.ADMISSION_PLAN_MULTI_VERTEX_UNSUPPORTED
            )
        return verdicts
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def multi_vertex_refusal(phase: str | None) -> bool:
    sources = {
        name: (FIXTURES / f"{name}.py").read_text(encoding="utf-8")
        for name in (ALL_ROOT, CHOICE_ROOT)
    }
    refused = refused_for_shape(sources)
    if phase == "full":
        return refused[ALL_ROOT] and refused[CHOICE_ROOT]
    if phase == "choice-only":
        return refused[CHOICE_ROOT] and not refused[ALL_ROOT]
    return refused[CHOICE_ROOT]


def spine_parametrization() -> set[str] | None:
    """The literal `SPINE_FIXTURES` tuple of the spine gate module, or None when it is absent or
    not a tuple of string literals."""
    try:
        tree = ast.parse(SPINE_GATE.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "SPINE_FIXTURES" for t in node.targets
        ):
            try:
                value = ast.literal_eval(node.value)
            except ValueError:
                return None
            if isinstance(value, tuple) and all(isinstance(v, str) for v in value):
                return set(value)
    return None


def one_vertex_spine_fixture(phase: str | None) -> bool:
    return (
        spine_parametrization() == {SPINE_FIXTURE} and (FIXTURES / f"{SPINE_FIXTURE}.py").is_file()
    )


PROBES = {
    "multi-vertex-refusal": multi_vertex_refusal,
    "one-vertex-spine-fixture": one_vertex_spine_fixture,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.proof.probes.single")
    parser.add_argument("entry", choices=sorted(PROBES))
    parser.add_argument("--phase", choices=PHASES, default=None)
    args = parser.parse_args(argv)
    return 0 if PROBES[args.entry](args.phase) else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
