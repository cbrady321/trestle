"""L.SV-0.1: K-11, the one-call long-job path in the three agent docs. Each doc's long-job guidance
names `run(..., completion="terminal")`; polling with a short `wait_ms` then `await_runs` stays only
as bounded-mode behavior."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.proof import kdoc

REPO_ROOT = Path(__file__).resolve().parents[3]
TERMINAL = 'completion="terminal"'

# doc -> (heading that opens its long-job section, regex of the next heading that closes it)
DOCS = {
    "docs/agents.md": ("## Wait and long jobs", r"^(?:## |---$)"),
    ".cursor/skills/trestle/SKILL.md": ("## Golden workflow", r"^## "),
    "docs/agent-console-mcp.md": ("## 3. Wait, blocking, and honesty", r"^(?:## |---$)"),
}


def _section(rel: str) -> str:
    heading, closer = DOCS[rel]
    lines = (REPO_ROOT / rel).read_text(encoding="utf-8").splitlines()
    start = lines.index(heading)
    end = next(
        (i for i in range(start + 1, len(lines)) if re.match(closer, lines[i])),
        len(lines),
    )
    return "\n".join(lines[start:end])


@pytest.mark.proves("WR-PROOF-10", "WR-PROOF-10:K-11", "A", "single", "INSPECT", "CI")
@pytest.mark.parametrize("rel", sorted(DOCS))
def test_k11_one_call_guidance_present(rel: str) -> None:
    text = (REPO_ROOT / rel).read_text(encoding="utf-8")
    section = _section(rel)
    assert TERMINAL in section, rel

    # the long-job line itself names the one-call path and does not steer to polling
    long_lines = [ln for ln in section.splitlines() if re.match(r"\**Long jobs", ln)]
    assert len(long_lines) == 1, (rel, long_lines)
    (line,) = long_lines
    assert TERMINAL in line, rel
    assert "short `wait_ms`" not in line, rel

    # "short wait_ms then await_runs" survives nowhere as long-job advice: a line saying "short
    # wait_ms" is scoped to bounded mode, and the `run_id`-first wait plus await_runs pair is gone
    for ln in text.splitlines():
        if "short `wait_ms`" in ln:
            assert "bounded" in ln, (rel, ln)
        assert not ("`run_id` quickly" in ln and "await_runs" in ln), (rel, ln)
    # bounded-mode polling guidance is kept, labelled as bounded mode
    assert "await_runs" in line and "bounded" in line, rel


def test_k11_landing_merge_is_sv0() -> None:
    (k11,) = [k for k in kdoc.load_k_doc_map() if k["id"] == "K-11"]
    assert k11["landing_merge"] == "SV-0" and k11["must_appear_by"] == "SV-0"
    assert sorted(k11["docs"]) == sorted(DOCS)
    for rel in k11["docs"]:
        assert (REPO_ROOT / rel).is_file(), rel
