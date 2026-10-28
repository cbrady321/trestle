"""The K-doc landing rule (CM-0 G-COMPAT; L.P0-0d.10): a landing commit
must carry its mapped doc files in the same merge. `fence merge`
(`L.P0-0d.11`) applies `missing_docs` to every landing before it pushes
(CM-3 P2); `meta kdoc` reports it (report mode; `--enforce` is
`L.CZ.3`'s).
"""

from __future__ import annotations

import argparse
import tomllib
from pathlib import Path

from tests.proof import fence as fence_mod
from tests.proof import trailers as trailers_mod

ROOT = Path(__file__).resolve().parents[2]
K_DOC_MAP_PATH = ROOT / "tests" / "proof" / "k_doc_map.toml"


def load_k_doc_map(path: Path | None = None) -> list[dict]:
    path = path or K_DOC_MAP_PATH
    return list(tomllib.loads(path.read_text()).get("k", []))


def missing_docs(merge_id: str, commit: str, cwd: Path | None = None, k_items=None) -> list[str]:
    """The doc files `k_doc_map` maps to `merge_id` that `commit`'s diff
    against its first parent does not touch. `commit` is read as the
    merge's landing commit (`trailers.landing`), never a `WR-Fix` — the
    caller passes that sha in."""
    cwd = cwd or ROOT
    k_items = k_items if k_items is not None else load_k_doc_map()
    mapped_docs: set[str] = set()
    for k in k_items:
        if k.get("landing_merge") == merge_id:
            mapped_docs.update(k.get("docs", []))
    if not mapped_docs:
        return []
    touched = set(fence_mod.diff_paths(cwd, f"{commit}^", commit))
    return sorted(mapped_docs - touched)


def cmd_kdoc(args: argparse.Namespace) -> int:
    """`python -m tests.proof.meta kdoc [--history <range>]` (report mode):
    reports every K-row closed without its named doc edit at its landing
    commit. Always exits 0 (enforce mode is `L.CZ.3`'s)."""
    k_items = load_k_doc_map()
    merges = sorted({k["landing_merge"] for k in k_items if k.get("landing_merge")})
    reported = False
    for merge_id in merges:
        commit = trailers_mod.landing(merge_id)
        if commit is None:
            continue
        missing = missing_docs(merge_id, commit, k_items=k_items)
        if missing:
            reported = True
            print(f"kdoc: {merge_id} landed at {commit} without editing: {missing}")
    if not reported:
        print("kdoc: every landed K-merge edited its mapped docs (report mode)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tests.proof.kdoc")
    parser.add_argument("--history", default=None)
    return parser


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(cmd_kdoc(build_parser().parse_args()))
