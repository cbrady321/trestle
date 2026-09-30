"""L.TR-L.12: the docs say what the tree band ships (WR-PROOF-10 docs check, MC-05, MC-B3-08).

`docs/agents.md` names every tree code and states the two open questions the band ships neutral
answers to (OQ-27, OQ-31); `docs/plugins.md` carries a composite example that publishes through the
validator and runs to a terminal answer as it stands."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from tests.tree import hostpath
from trestle.common import codes
from trestle.common.plan import vocabulary
from trestle.common.types import PublishView, RunView
from trestle.server.main import Kernel
from trestle.workflow import codes as workflow_codes

REPO = Path(__file__).resolve().parents[2]
AGENTS = REPO / "docs" / "agents.md"
PLUGINS = REPO / "docs" / "plugins.md"
TRESTLE = REPO / "trestle"

# Every wire code the tree band adds (a2 L.TR-L.12's closed list): what publication refuses on the
# declaration alone, what admission refuses over the compiled tree, the one stop a started run can
# give for a declaration that is no longer the admitted one, and the root-only cancel refusal.
TREE_CODES = (
    "publication.unit_unresolved",
    "publication.dependency_cycle",
    "publication.declaration_conflict",
    "publication.plan_precondition_uncovered",
    "admission.unit_unresolved",
    "admission.dependency_cycle",
    "admission.declaration_conflict",
    "admission.lease_set_undecidable",
    "admission.unknown_identifier",
    "execution.declaration_stale",
    "projection.cancel_not_root",
)

# the modules that define a wire code's spelling; a producer is a reference to it elsewhere
DEFINITIONS = (
    REPO / "trestle" / "common" / "codes.py",
    REPO / "trestle" / "common" / "plan" / "vocabulary.py",
    REPO / "trestle" / "workflow" / "codes.py",
)


def _defined_names(code: str) -> set[str]:
    """Every name under which `code` is bound in the code modules (a code is reached by its
    constant, so the constant's name is what a producer refers to)."""
    names: set[str] = set()
    for module in (codes, vocabulary, workflow_codes):
        names |= {
            name
            for name, value in vars(module).items()
            if isinstance(value, str) and value == code and name.isupper()
        }
    return names


def producers(code: str) -> list[str]:
    """Files under `trestle/` (outside the modules that define the spelling) that name the code:
    by a constant that holds it, or by its literal."""
    names = _defined_names(code)
    found: list[str] = []
    for path in sorted(TRESTLE.rglob("*.py")):
        if path in DEFINITIONS:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                (isinstance(node, ast.Name) and node.id in names)
                or (isinstance(node, ast.Attribute) and node.attr in names)
                or (isinstance(node, ast.Constant) and node.value == code)
            ):
                found.append(str(path.relative_to(REPO)))
                break
    return found


def test_tree_codes_documented_and_produced() -> None:
    """Each tree code is defined in `codes.py` (by re-export or its own spelling), appears in
    `docs/agents.md`, and has a producer in `trestle/`."""
    agents = AGENTS.read_text(encoding="utf-8")
    for code in TREE_CODES:
        assert _defined_names(code), f"{code}: no constant holds it in the code modules"
        assert f"`{code}`" in agents, f"{code}: not in docs/agents.md"
        assert producers(code), f"{code}: nothing in trestle/ produces it"


def test_the_producer_check_rejects_a_code_nothing_names() -> None:
    """The check is not vacuous: a spelling no constant holds and no source names has neither."""
    invented = "publication.tree_code_nobody_raises"
    assert not _defined_names(invented) and producers(invented) == []


def test_open_questions_disclosed() -> None:
    """`docs/agents.md` states OQ-27 and OQ-31 as open, says what is shipped for each, and names
    `admit_and_stop` as the pre-existing in-node stop, not a decision (hld-wr-contract item 4)."""
    text = AGENTS.read_text(encoding="utf-8")
    section = text[text.index("## Composite workflows (trees)") :]
    section = section[: section.index("\n---\n")]
    flat = " ".join(section.split())

    oq27 = re.search(r"open question OQ-27\*\*", flat)
    assert oq27, "OQ-27 is not stated as open"
    assert "projection.cancel_not_root" in flat and "assumed answer" in flat

    assert re.search(r"\*\*Root-entry eligibility is open \(OQ-31\)", flat), "OQ-31 not open"
    assert "`admit_and_stop`" in flat
    assert "pre-existing in-node stop, not a decision" in flat


def test_choice_root_admitted_and_code_retired_is_documented() -> None:
    """The docs say a `ChoiceNode` root is admitted (L.TR-5.3) and that
    `admission.plan_multi_vertex_unsupported` is retired: still a defined code, no producer."""
    for doc in (AGENTS, PLUGINS):
        flat = " ".join(doc.read_text(encoding="utf-8").split())
        assert "ChoiceNode" in flat, doc.name
        assert "admission.plan_multi_vertex_unsupported" in flat and "retired" in flat, doc.name
        assert "still refused" not in flat and "until its selection" not in flat, doc.name
    assert (
        codes.ADMISSION_PLAN_MULTI_VERTEX_UNSUPPORTED == "admission.plan_multi_vertex_unsupported"
    )


def _example() -> str:
    text = PLUGINS.read_text(encoding="utf-8")
    pattern = r"<!-- tree-example -->\n```python\n(.*?)\n```\n<!-- /tree-example -->"
    block = re.search(pattern, text, re.S)
    assert block, "docs/plugins.md has no marked composite example"
    return block.group(1) + "\n"


def test_plugins_example_publishes_through_the_validator_and_runs(tree_kernel: Kernel) -> None:
    """The example in `docs/plugins.md` publishes as written and, run through the host, reaches a
    terminal `succeeded` answer for its three steps (an example that does not work is not one)."""
    published = hostpath.publish_tree_via_host(tree_kernel, _example())
    assert isinstance(published, PublishView), published
    assert published.name == "release"

    view = hostpath.run_tree_via_host(tree_kernel, published.name, {"env": "dev"})
    assert isinstance(view, RunView), view
    assert view.state == "succeeded", view
