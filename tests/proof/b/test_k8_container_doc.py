"""L.RB-12.6: the K-8 docs state the container half (LOGIC · CI).

K-8's doc files are the ones `tests/proof/k_doc_map.toml` maps it to (MC-05). The K-8 block of each
(`<!-- K-8 -->` ... `<!-- /K-8 -->`) must say, besides processes, that a run-created container is
stopped when a run ends, a passed run included: the behaviour `host/test_own3_container_half.py`
falsifies on real Docker. This leaf checks the statement only; the text landed with K-8's
container half on master.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
K_DOC_MAP = ROOT / "tests" / "proof" / "k_doc_map.toml"
BLOCK = re.compile(r"<!-- K-8 -->(.*?)<!-- /K-8 -->", re.S)


def k8_docs() -> list[Path]:
    entries = tomllib.loads(K_DOC_MAP.read_text(encoding="utf-8"))["k"]
    (k8,) = [k for k in entries if k["id"] == "K-8"]
    return [ROOT / doc for doc in k8["docs"]]


def container_half(text: str) -> bool:
    """The block names run-created containers, their stop, and the passed run."""
    lowered = " ".join(text.lower().split())
    return (
        "run-created container" in lowered
        and "stopped" in lowered
        and ("passed run" in lowered or "succeeded" in lowered)
    )


def test_k8_docs_state_container_half() -> None:
    docs = k8_docs()
    assert docs, "K-8 maps to at least one doc file"
    for doc in docs:
        blocks = BLOCK.findall(doc.read_text(encoding="utf-8"))
        assert len(blocks) == 1, f"{doc}: one K-8 block"
        assert container_half(blocks[0]), f"{doc}: the K-8 block states no container half"


def test_the_check_is_not_vacuous() -> None:
    processes_only = "A succeeded run leaves no attributable process behind; it is stopped."
    assert not container_half(processes_only)
    assert container_half("Every run-created container is stopped; a passed run is no exception.")
