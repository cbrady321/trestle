"""The CK decline drill (L.CS-1.4): CM-7's decline patch, computed and checked.

A maintainer "no" to a confirm-gated K-item is applied by one PR on the CK
merge's gate branch (CM-7, MC-CORE-12). This module derives that patch from
what the plan already records and checks CM-7's two drill conditions; it
restates none of CM-7. The drill never edits the checkout it is run from: it
works in a scratch worktree, removed on exit.

`DECLINES` maps each CK merge id to the `DECLINE` of its own module,
`tests/core/tooling/declines/<ck>.py`, written by that item's leaf in its own
lane and assembled here at import. A `DECLINE` names the switch and its
declined value, the K-items whose `<!-- K-n -->` blocks it drops, the register
entries it restores and the labels (and clauses) it renders `na("K-n
declined")`. It never lists a file: the files of the patch are CM-7's derived
set.

`DECLINE` schema (closed):

    {"merge": "CK-8",                              # required; the module stem is its slug
     "k": "K-8"  |  ["K-3", "K-4"],                # required; the first is the citation's
     "switch": {"module": "trestle.server.conductor",     # a module constant ...
                "name": "REAP_ON_SUCCESS", "declined": False}
              | {"command": {"from": "mypy",              # ... or a CI/REG step's command
                             "to": "python -m tests.proof.meta mypy-ratchet --max 1"}},
     "restores": ["T-3"],                          # register entry ids, optional
     "labels": ["WR-PROOF-10:K-8", ...],           # CSC-1 label ids, optional
     "clauses": ["A8.2"],                          # MC-03 clause ids, optional
     "variants": ["tests/x.py::test_variant"]}     # switch-tied variant nodes, optional
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tests.proof import fence as fence_mod
from tests.proof import trailers as trailers_mod

ROOT = Path(__file__).resolve().parents[3]
DECLINES_DIR = Path(__file__).resolve().parent / "declines"

REGISTER_PATH = "tests/proof/temporary.toml"
REGISTER_D_DIR = "tests/proof/temporary.d"
LABELS_D_DIR = "tests/proof/labels.d"
NAMED_NOT_REMOVED = "named-not-removed"

# Set in the scratch run's environment: the nested pytest never re-enters the drill.
NESTED_ENV = "TRESTLE_CK_DRILL_NESTED"
# Set by the `ck-isolation` CI job: the real-history drill runs only there, or when named.
ISOLATION_ENV = "TRESTLE_CK_ISOLATION"


class DeclineError(ValueError):
    """A declines module or its `DECLINE` violates MC-CORE-12's shape."""


class DrillFailure(AssertionError):
    """CM-7's drill condition (a) or (b), or its scope rule, does not hold."""


# ---------------------------------------------------------------------------
# DECLINES: assembled at import from declines/<ck>.py (MC-CORE-12)
# ---------------------------------------------------------------------------

_ALLOWED_KEYS = {"merge", "k", "switch", "restores", "labels", "clauses", "variants"}
_REQUIRED_KEYS = {"merge", "k", "switch"}
_PATHLIKE = re.compile(r"[\w.-]*/[\w./-]+|[\w-]+\.(?:py|md|toml|ya?ml|json|txt|cfg|ini)\b")


def slug(merge_id: str) -> str:
    """`CK-3/4` -> `ck_3_4`: the stem of the merge's declines module."""
    return re.sub(r"[-/]", "_", merge_id).lower()


def param_id(merge_id: str) -> str:
    """`CK-3/4` -> `CK-3-4`: the pytest parameter id the CK leaves' MJ steps cite."""
    return merge_id.replace("/", "-")


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def _ks(decline: dict[str, Any]) -> list[str]:
    k = decline["k"]
    return [k] if isinstance(k, str) else list(k)


def validate_decline(decline: Any, source: str) -> dict[str, Any]:
    if not isinstance(decline, dict):
        raise DeclineError(f"{source}: DECLINE is not a dict")
    extra = set(decline) - _ALLOWED_KEYS
    missing = _REQUIRED_KEYS - set(decline)
    if extra or missing:
        raise DeclineError(
            f"{source}: DECLINE has bad keys (extra={sorted(extra)}, missing={sorted(missing)}); "
            "an entry never lists files"
        )
    merge = decline["merge"]
    if not isinstance(merge, str) or not re.fullmatch(r"CK-\d+(?:/\d+)?", merge):
        raise DeclineError(f"{source}: merge {merge!r} is not a CK merge id")
    if Path(source).stem != slug(merge):
        raise DeclineError(f"{source}: module stem must be {slug(merge)!r} for merge {merge}")
    ks = _ks(decline)
    if not ks or not all(isinstance(k, str) and re.fullmatch(r"K-\d+", k) for k in ks):
        raise DeclineError(f"{source}: k must be a K-n id or a list of them")
    switch = decline["switch"]
    if not isinstance(switch, dict) or not (
        (set(switch) == {"module", "name", "declined"})
        or (set(switch) == {"command"} and set(switch["command"]) == {"from", "to"})
    ):
        raise DeclineError(f"{source}: switch must be a module constant or a command pair")
    for key in ("restores", "labels", "clauses", "variants"):
        value = decline.get(key, [])
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise DeclineError(f"{source}: {key} must be a list of strings")
    for text in _strings({k: v for k, v in decline.items() if k not in ("merge", "variants")}):
        if _PATHLIKE.search(text):
            raise DeclineError(f"{source}: DECLINE lists a file ({text!r}); it never lists files")
    return decline


def assemble_declines(directory: Path | None = None) -> dict[str, dict[str, Any]]:
    """One module per CK item, each defining `DECLINE`; keyed by merge id."""
    directory = directory or DECLINES_DIR
    found: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.glob("*.py")):
        if path.name == "__init__.py":
            continue
        spec = importlib.util.spec_from_file_location(f"_ck_decline_{path.stem}", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if not hasattr(module, "DECLINE"):
            raise DeclineError(f"{path.name}: module defines no DECLINE")
        decline = validate_decline(module.DECLINE, str(path))
        if decline["merge"] in found:
            raise DeclineError(f"{path.name}: second DECLINE for {decline['merge']}")
        found[decline["merge"]] = decline
    return found


DECLINES: dict[str, dict[str, Any]] = assemble_declines()


# ---------------------------------------------------------------------------
# git helpers
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise DrillFailure(f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout


def _show(repo: Path, rev: str, path: str) -> str | None:
    proc = subprocess.run(
        ["git", "show", f"{rev}:{path}"], cwd=repo, capture_output=True, text=True
    )
    return proc.stdout if proc.returncode == 0 else None


def changed_files(repo: Path, base: str, head: str) -> list[str]:
    return [p for p in _git(repo, "diff", "--name-only", base, head).splitlines() if p]


def changed_line_ranges(repo: Path, base: str, head: str, path: str) -> list[tuple[int, int]]:
    """Line ranges of `path` at `head` that differ from `base` (new-side hunks)."""
    out = _git(repo, "diff", "-U0", base, head, "--", path)
    ranges = []
    for match in re.finditer(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@", out, re.M):
        start, count = int(match.group(1)), int(match.group(2) or 1)
        if count:
            ranges.append((start, start + count - 1))
    return ranges


@dataclass
class Hunk:
    """One hunk of M's per-file diff (`git diff -U1 base head`): its text, and the merge-side
    (new-side) line numbers of the lines it adds."""

    text: str
    added: set[int]


def file_hunks(repo: Path, base: str, head: str, path: str) -> tuple[str, list[Hunk]]:
    """(the per-file diff header, its hunks) of `path` between `base` and `head`, one line of
    context each side, so a hunk reverts cleanly only where its own lines and their immediate
    neighbours still read as M left them."""
    out = _git(repo, "diff", "-U1", "--no-color", base, head, "--", path)
    header: list[str] = []
    hunks: list[Hunk] = []
    lines: list[str] = []
    added: set[int] = set()
    new = 0
    for line in out.splitlines(keepends=True):
        match = re.match(r"^@@ -\S+ \+(\d+)(?:,\d+)? @@", line)
        if match:
            if lines:
                hunks.append(Hunk("".join(lines), added))
            lines, added, new = [line], set(), int(match.group(1))
        elif not lines:
            header.append(line)
        else:
            lines.append(line)
            if line.startswith("+"):
                added.add(new)
                new += 1
            elif line.startswith(" "):
                new += 1
    if lines:
        hunks.append(Hunk("".join(lines), added))
    return "".join(header), hunks


def _reverse_apply(text: str, path: str, header: str, hunk: Hunk) -> str | None:
    """`text` with `hunk` reverse-applied (`git apply -R`, no fuzz: every line of the hunk, its
    one line of context included, must match, at any offset), or `None` when it does not apply."""
    with tempfile.TemporaryDirectory(prefix="ck-hunk-") as tmp:
        target = Path(tmp) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        patch_file = Path(tmp) / "hunk.patch"
        patch_file.write_text(header + hunk.text)
        proc = subprocess.run(
            ["git", "apply", "-R", "--whitespace=nowarn", str(patch_file)],
            cwd=tmp,
            capture_output=True,
            text=True,
        )
        return target.read_text() if proc.returncode == 0 else None


def _unguarded_lines(source: str, name: str, lines: set[int]) -> set[int]:
    """The lines of `lines` some call site in `source` spans that does not read the switch
    `name` (CM-7 (b)'s AST rule)."""
    tree = ast.parse(source)
    parents = _parents(tree)
    hit: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        span = set(range(node.lineno, (node.end_lineno or node.lineno) + 1))
        if span & lines and not _call_reads_switch(node, parents, name):
            hit |= span & lines
    return hit


def revert_unguarded_hunks(
    repo: Path, span: Span, path: str, name: str, text: str
) -> tuple[str, set[int]]:
    """CM-7 step 3, amended (L.CS-1.4.fix1): in a product file of M's lane globs that a later
    commit changed, each hunk of M's diff with a changed call site that does not read the switch
    is reverse-applied at HEAD when it still applies cleanly, so the decline restores M's
    predecessor text there as step 2 does for an untouched file. Hunks whose calls read the switch
    stay (the switch carries them); a hunk that no longer applies stays, for (b) to judge.
    Returns (the new text, the merge-side added lines of the reverted hunks)."""
    source = _show(repo, span.head, path)
    if source is None or _show(repo, span.base, path) is None:
        return text, set()  # a file M added (or deleted) is a whole-file matter, never a hunk's
    header, hunks = file_hunks(repo, span.base, span.head, path)
    reverted: set[int] = set()
    for hunk in reversed(hunks):  # bottom first: earlier hunks keep their line numbers
        if not _unguarded_lines(source, name, hunk.added):
            continue
        undone = _reverse_apply(text, path, header, hunk)
        if undone is not None:
            text = undone
            reverted |= hunk.added
    return text, reverted


def _later_changed(repo: Path, landing: str | None, path: str) -> bool:
    """`git log <landing>..HEAD -- <file>` is not empty (CM-7 step 2)."""
    if landing is None:
        return False
    return bool(_git(repo, "log", "--format=%H", f"{landing}..HEAD", "--", path).strip())


def lane_globs_for(merge_id: str, cfg: fence_mod.FenceConfig | None = None) -> list[str]:
    """The `globs` of the lane whose branch carries `merge_id`'s gate (CM-2)."""
    cfg = cfg or fence_mod.load_fence()
    (gate,) = [g for g in cfg.gates if g.merge == merge_id]
    (lane,) = [lane for lane in cfg.lanes if gate.branch.startswith(lane.branch_prefix)]
    return list(lane.globs)


@dataclass
class Span:
    """The CK merge's diff: `base..head`; `landing` is its carrier commit (on a bundle PR, its
    `Bundle-Merge:` marker), or None on its own PR."""

    base: str
    head: str
    landing: str | None


def span_for(repo: Path, merge_id: str, *, pr_base: str | None = None) -> Span:
    """The landing diff `trailers.landing(M)` against its first parent; before M lands (its
    own PR, its CK leaves' verifiers) the PR diff X..H, X the current origin/master. On a
    bundle PR (`Bundle-Merge:` markers) the PR diff is every gate's: M's own is its chunk
    (`bundle_span`)."""
    sha = trailers_mod.landing(merge_id, "HEAD", repo)
    if sha is not None:
        return Span(base=f"{sha}^1", head=sha, landing=sha)
    base = pr_base
    if base is None:
        base = _git(repo, "merge-base", "HEAD", "origin/master").strip()
    chunk = bundle_span(repo, merge_id, base)
    if chunk is not None:
        return chunk
    return Span(base=base, head="HEAD", landing=None)


def _marked_line(repo: Path, base: str) -> tuple[str, list[trailers_mod.Commit]] | None:
    """The bundle branch's tip and its first-parent commits after `base` (oldest first), when
    that line carries `Bundle-Merge:` markers; `None` on an ordinary PR. The tip is HEAD, or on a
    PR's merge ref (`refs/pull/N/merge`, HEAD's first parent the base) the parent that is the
    bundle head; a marker off that line (a lane branch's) is never read."""
    parents = _git(repo, "rev-list", "--parents", "-n", "1", "HEAD").split()[1:]
    for tip in ["HEAD", *parents[1:]]:
        commits = trailers_mod._commits(f"{base}..{tip}", cwd=repo)  # noqa: SLF001
        if any(c.bundle_markers() for c in commits):
            return tip, commits
    return None


def bundle_span(repo: Path, merge_id: str, base: str) -> Span | None:
    """M's chunk of a bundle branch (CM-2 boundaries, as `fence.resolve_bundle` reads them): from
    the previous gate's `Bundle-Merge:` marker (the fork point for the first gate) to M's own
    marker, which is M's landing for CM-7 step 2: a chunk file a later chunk changed is left in
    place, like a file a later merge changed after M landed. M with no marker yet is the trailing
    chunk (last marker..tip, nothing later). `None` when the branch carries no markers."""
    found = _marked_line(repo, base)
    if found is None:
        return None
    tip, commits = found
    previous: str | None = None
    for commit in commits:
        marks = commit.bundle_markers()
        if merge_id in marks:
            start = previous or _git(repo, "merge-base", tip, base).strip()
            return Span(base=start, head=commit.sha, landing=commit.sha)
        if marks:
            previous = commit.sha
    assert previous is not None
    return Span(base=previous, head=tip, landing=None)


# ---------------------------------------------------------------------------
# the derived patch (CM-7 steps 1-5)
# ---------------------------------------------------------------------------


@dataclass
class Patch:
    edits: dict[str, str | None] = field(default_factory=dict)  # path -> new text; None = delete
    scope: set[str] = field(default_factory=set)  # CM-7's derived set
    reverted: set[str] = field(default_factory=set)  # step 2
    left: set[str] = field(default_factory=set)  # step 3: the diff's files left in place
    # step 3, amended: per left file, the merge-side line numbers of M's added lines whose hunks
    # were reverse-applied at HEAD (only hunks with a changed call site that ignores the switch)
    hunks_reverted: dict[str, set[int]] = field(default_factory=dict)


def _read(repo: Path, edits: dict[str, str | None], path: str) -> str | None:
    if path in edits:
        return edits[path]
    file = repo / path
    return file.read_text() if file.is_file() else None


def _switch_pattern(name: str) -> re.Pattern[str]:
    return re.compile(
        rf"^({re.escape(name)}(?:\s*:\s*[^=\n]+?)?\s*=\s*)(True|False|None|\d+|\"[^\"\n]*\"|'[^'\n]*')",
        re.M,
    )


def switch_file(repo: Path, decline: dict[str, Any]) -> str | None:
    module = decline["switch"].get("module")
    if module is None:
        return None
    base = module.replace(".", "/")
    for candidate in (f"{base}.py", f"{base}/__init__.py"):
        if (repo / candidate).is_file():
            return candidate
    raise DrillFailure(f"switch module {module} has no file at HEAD")


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{k} = {_toml_value(v)}" for k, v in value.items()) + " }"
    raise TypeError(type(value))


_ENTRY_ORDER = (
    "id",
    "mechanism",
    "introduced_by",
    "serves",
    "phases",
    "probe",
    "removal_condition",
    "removed_by",
    "permanent",
    "citation",
)


def _entry_block(entry: dict[str, Any]) -> str:
    keys = [k for k in _ENTRY_ORDER if k in entry] + [k for k in entry if k not in _ENTRY_ORDER]
    return "[[entry]]\n" + "".join(f"{k} = {_toml_value(entry[k])}\n" for k in keys)


def _put_entry(text: str, entry: dict[str, Any]) -> str:
    """`entry` in `text`: in place when step 2 already restored the file to its base (the entry is
    still there with its original `removed_by`), else appended."""
    lines = text.split("\n")
    target = re.compile(rf'^id\s*=\s*"{re.escape(entry["id"])}"\s*$')
    for i, line in enumerate(lines):
        if target.match(line):
            start = i
            while start > 0 and not lines[start].startswith("[[entry]]"):
                start -= 1
            end = i + 1
            while end < len(lines) and not lines[end].startswith("[["):
                end += 1
            tail = lines[end:]
            block = _entry_block(entry).rstrip("\n").split("\n")
            gap = [""] if tail else []
            return "\n".join(lines[:start] + block + gap + tail)
    return text.rstrip("\n") + ("\n\n" if text.strip() else "") + _entry_block(entry)


def register_files(repo: Path, rev: str | None = None) -> list[str]:
    """The register's TOML files: at `rev` (a commit) or in the working tree."""
    if rev is None:
        files = [REGISTER_PATH] if (repo / REGISTER_PATH).is_file() else []
        d = repo / REGISTER_D_DIR
        files += (
            sorted(f"{REGISTER_D_DIR}/{p.name}" for p in d.glob("*.toml")) if d.is_dir() else []
        )
        return files
    names = _git(repo, "ls-tree", "--name-only", "-r", rev, "tests/proof").splitlines()
    return [n for n in names if n == REGISTER_PATH or n.startswith(f"{REGISTER_D_DIR}/")]


def _entries_in(text: str) -> list[dict[str, Any]]:
    return list(tomllib.loads(text).get("entry", []))


def _find_original_entry(repo: Path, span: Span, entry_id: str) -> tuple[str, dict[str, Any]]:
    for path in register_files(repo, span.base):
        text = _show(repo, span.base, path)
        for entry in _entries_in(text or ""):
            if entry["id"] == entry_id:
                return path, entry
    raise DrillFailure(f"register entry {entry_id} is not in the register at {span.base}")


def _label_block_bounds(lines: list[str], label_id: str) -> tuple[int, int] | None:
    target = re.compile(rf'^id\s*=\s*"{re.escape(label_id)}"\s*$')
    for i, line in enumerate(lines):
        if target.match(line):
            start = i
            while start > 0 and not lines[start].startswith("[[label]]"):
                start -= 1
            end = i + 1
            while end < len(lines) and not lines[end].startswith("[["):
                end += 1
            return start, end
    return None


def _na_label(text: str, label_id: str, reason: str) -> str | None:
    lines = text.split("\n")
    bounds = _label_block_bounds(lines, label_id)
    if bounds is None:
        return None
    start, end = bounds
    block = [ln for ln in lines[start:end] if not re.match(r"^reason\s*=", ln)]
    for i, ln in enumerate(block):
        if re.match(r"^posture\s*=", ln):
            block[i : i + 1] = ['posture = "na"', f"reason = {json.dumps(reason)}"]
            break
    else:
        return None
    return "\n".join(lines[:start] + block + lines[end:])


def derive_patch(repo: Path, decline: dict[str, Any], lane_globs: list[str], span: Span) -> Patch:
    """CM-7's decline patch for `decline`, computed at `repo`'s HEAD. Steps 1-5 in order; the
    derived set `scope` is the union of what each step touches, never a hand-kept list."""
    patch = Patch()
    edits = patch.edits
    ks = _ks(decline)
    primary = ks[0]
    diff = changed_files(repo, span.base, span.head)

    # step 1: flip the switch, drop the K-doc blocks
    switch = decline["switch"]
    if "module" in switch:
        path = switch_file(repo, decline)
        assert path is not None
        text = _read(repo, edits, path) or ""
        pattern = _switch_pattern(switch["name"])
        if not pattern.search(text):
            raise DrillFailure(f"switch {switch['module']}.{switch['name']} is not defined")
        edits[path] = pattern.sub(lambda m: f"{m.group(1)}{switch['declined']!r}", text, count=1)
        patch.scope.add(path)
    else:
        pair = switch["command"]
        pattern = re.compile(rf"^(\s*(?:- )?run: ){re.escape(pair['from'])}\s*$", re.M)
        hits = [
            f
            for f in diff
            if fence_mod.glob_match(f, lane_globs)
            and (repo / f).is_file()
            and pattern.search((repo / f).read_text())
        ]
        if not hits:
            raise DrillFailure(f"no command step {pair['from']!r} in the CK merge's files")
        for f in hits:
            edits[f] = pattern.sub(
                lambda m: f"{m.group(1)}{pair['to']}", _read(repo, edits, f) or ""
            )
            patch.scope.add(f)
    for k in ks:
        marker = f"<!-- {k} -->"
        files = _git(repo, "grep", "-l", "-F", marker, check=False).split()
        if not files:
            raise DrillFailure(f"no {marker} block in the tree")
        block = re.compile(rf"^[ \t]*{re.escape(marker)}.*?<!-- /{k} -->[ \t]*\n?", re.M | re.S)
        for f in files:
            edits[f] = block.sub("", _read(repo, edits, f) or "")
            patch.scope.add(f)

    # steps 2-3: revert the lane-glob files no later commit changed; leave the rest in place,
    # except that in a left product file of the lane globs M's hunks with an un-gated changed call
    # site are reverse-applied where they still apply cleanly (step 3 as amended, L.CS-1.4.fix1)
    for f in diff:
        in_globs = fence_mod.glob_match(f, lane_globs)
        if in_globs and not _later_changed(repo, span.landing, f):
            patch.reverted.add(f)
            edits[f] = _show(repo, span.base, f)  # None deletes a file the merge added
            patch.scope.add(f)
            continue
        patch.left.add(f)
        name = switch.get("name")
        current = _read(repo, edits, f)
        if in_globs and name and f.endswith(".py") and not is_test_path(f) and current is not None:
            new_text, reverted = revert_unguarded_hunks(repo, span, f, name, current)
            if reverted:
                edits[f] = new_text
                patch.scope.add(f)
                patch.hunks_reverted[f] = reverted

    # step 4: restore the register entries, named-not-removed
    for entry_id in decline.get("restores", []):
        path, original = _find_original_entry(repo, span, entry_id)
        restored = dict(
            original, removed_by=NAMED_NOT_REMOVED, citation=f"{primary} declined", serves=[]
        )
        edits[path] = _put_entry(_read(repo, edits, path) or "", restored)
        patch.scope.add(path)

    # step 5: the labels na("K-n declined")
    label_files = sorted(f"{LABELS_D_DIR}/{p.name}" for p in (repo / LABELS_D_DIR).glob("*.toml"))
    for label_id in decline.get("labels", []):
        for path in label_files:
            updated = _na_label(_read(repo, edits, path) or "", label_id, f"{primary} declined")
            if updated is not None:
                edits[path] = updated
                patch.scope.add(path)
                break
        else:
            raise DrillFailure(f"label {label_id} is not declared in {LABELS_D_DIR}")
    return patch


def apply_patch(root: Path, patch: Patch) -> None:
    for path, text in patch.edits.items():
        file = root / path
        if text is None:
            file.unlink(missing_ok=True)
        else:
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(text)


def touched_paths(root: Path) -> set[str]:
    """Every path `git status` reports changed in `root`'s working tree."""
    out = _git(root, "status", "--porcelain", "--untracked-files=all")
    paths = set()
    for line in out.splitlines():
        entry = line[3:]
        paths.add(entry.split(" -> ")[-1].strip('"'))
    return paths


def assert_within_scope(touched: set[str], scope: set[str]) -> None:
    """A decline patch touches nothing outside CM-7's derived set."""
    outside = sorted(touched - scope)
    if outside:
        raise DrillFailure(f"decline patch touches files outside the derived set: {outside}")


# ---------------------------------------------------------------------------
# drill conditions
# ---------------------------------------------------------------------------


def _reads_switch(node: ast.AST, name: str) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id == name:
            return True
        if isinstance(sub, ast.Attribute) and sub.attr == name:
            return True
    return False


def _ends_flow(body: list[ast.stmt]) -> bool:
    return bool(body) and isinstance(body[-1], (ast.Return, ast.Raise, ast.Continue, ast.Break))


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}


def _call_reads_switch(call: ast.Call, parents: dict[ast.AST, ast.AST], name: str) -> bool:
    """A call site "reads the switch" when its own text does, when a conditional enclosing it
    tests it, or when a guard clause before it in an enclosing block (`if not S: return`) does."""
    if _reads_switch(call, name):
        return True
    node: ast.AST = call
    while node in parents:
        parent = parents[node]
        if isinstance(parent, (ast.If, ast.IfExp, ast.While)) and _reads_switch(parent.test, name):
            return True
        if isinstance(parent, ast.comprehension) and any(
            _reads_switch(c, name) for c in parent.ifs
        ):
            return True
        for attr in ("body", "orelse", "finalbody"):
            block = getattr(parent, attr, None)
            if isinstance(block, list) and node in block:
                for before in block[: block.index(node)]:
                    if (
                        isinstance(before, ast.If)
                        and _reads_switch(before.test, name)
                        and _ends_flow(before.body)
                    ):
                        return True
        node = parent
    return False


def is_test_path(path: str) -> bool:
    """A test file: under a `tests/` directory (the repo's, or a package's)."""
    return path.startswith("tests/") or "/tests/" in path


def switch_isolation_violations(
    repo: Path, decline: dict[str, Any], span: Span, patch: Patch
) -> list[str]:
    """CM-7 (b): every call site the CK merge changed, in a file step 3 leaves in place,
    reads the CK's switch (AST). A command switch has no call sites to read it. A changed line
    whose hunk step 3 reverse-applied at HEAD (`Patch.hunks_reverted`) is M's predecessor text
    again on the declined tree, so it is not judged; every other changed line still is.

    Only product source is checked (L.P0-0d.29). Step 3 puts behind the switch the behaviour a
    CK adds; a test file left in place adds no behaviour, and its nodes are judged by (a): a node
    carrying the CK's labels renders na and is deselected, and every other node must pass REG on
    the declined tree. So a later marker edit to a CK-changed test file (for example the A9.3
    marker on test_cl_c2_strict.py) is not a switch-isolation failure."""
    switch = decline["switch"]
    name = switch.get("name")
    if name is None:
        return []
    violations = []
    for path in sorted(patch.left):
        if is_test_path(path):
            continue
        source = _show(repo, span.head, path)  # the merge's own version: its line numbers
        if not path.endswith(".py") or source is None:
            continue
        ranges = changed_line_ranges(repo, span.base, span.head, path)
        if not ranges:
            continue
        reverted = patch.hunks_reverted.get(path, set())
        judged = {n for lo, hi in ranges for n in range(lo, hi + 1)} - reverted
        tree = ast.parse(source)
        parents = _parents(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            end = node.end_lineno or node.lineno
            if not judged & set(range(node.lineno, end + 1)):
                continue
            if not _call_reads_switch(node, parents, name):
                violations.append(f"{path}:{node.lineno}: changed call site does not read {name}")
    return violations


def register_entries_of(text: str) -> dict[str, dict[str, Any]]:
    return {entry["id"]: entry for entry in _entries_in(text)}


def register_entries(root: Path) -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    for path in register_files(root):
        for entry in _entries_in((root / path).read_text()):
            entries[entry["id"]] = entry
    return entries


def declined_state_violations(root: Path, decline: dict[str, Any]) -> list[str]:
    """CM-7 (a), the static half, on the patched tree: the K blocks are gone, the switch holds
    its declined value, every restored entry has the decline shape, every label is na."""
    problems = []
    ks = _ks(decline)
    primary = ks[0]
    for k in ks:
        if _git(root, "grep", "-l", "-F", f"<!-- {k} -->", check=False).strip():
            problems.append(f"the {k} doc block is still present")
    switch = decline["switch"]
    if "module" in switch:
        path = switch_file(root, decline)
        if path is not None:
            match = _switch_pattern(switch["name"]).search((root / path).read_text())
            if match is not None and match.group(2) != repr(switch["declined"]):
                problems.append(f"{switch['name']} is {match.group(2)}, not {switch['declined']!r}")
    entries = register_entries(root)
    for entry_id in decline.get("restores", []):
        entry = entries.get(entry_id)
        if entry is None:
            problems.append(f"register entry {entry_id} is not restored")
        elif (
            entry["removed_by"] != NAMED_NOT_REMOVED
            or entry.get("citation") != f"{primary} declined"
            or entry["serves"] != []
        ):
            problems.append(
                f"register entry {entry_id} is not named-not-removed / '{primary} declined' / "
                "serves = []"
            )
    labels: dict[str, dict[str, Any]] = {}
    for path in sorted((root / LABELS_D_DIR).glob("*.toml")):
        for label in tomllib.loads(path.read_text()).get("label", []):
            labels[label["id"]] = label
    for label_id in decline.get("labels", []):
        label = labels.get(label_id)
        if (
            label is None
            or label.get("posture") != "na"
            or label.get("reason") != f"{primary} declined"
        ):
            problems.append(f"label {label_id} is not na('{primary} declined')")
    return problems


# ---------------------------------------------------------------------------
# the real regression: REG and the ledger render with the patch applied
# ---------------------------------------------------------------------------


@dataclass
class RegressionResult:
    failures: list[str] = field(default_factory=list)
    proven: list[str] = field(default_factory=list)  # clauses/labels the ledger renders PROVEN


Regression = Callable[[Path, dict[str, Any]], RegressionResult]

# Deselected from the drill's REG pytest (speed plan item 7). `test_baseline.py` compares the
# environment's mypy count with EV-01 and re-runs the corpus; in the scratch run mypy still finds
# the installed (typed) trestle_packs, so it reads the environment, not the declined tree. The
# `test` job runs it on the same head.
REG_NOT_RUN = ("tests/proof/selftest/test_baseline.py",)

# REG in parallel (speed plan L3/C5). REG's serial `pytest -q` step runs as proven partitions, the
# reading CI's `test` job already makes (L.P0-0d.17): its node ids are collected once with that
# step's own arguments, dealt by whole file over REG_WORKERS concurrent pytest processes, and the
# nodes that cannot share a host with another suite run after them, serially. Every process
# records the node ids it selected (`pytest_collection_finish` below); REG fails unless those sets
# are disjoint and their union is the serial collection exactly. Same tree, same nodes, same pass
# rule (every process exits 0); only the order and the process boundaries differ.
REG_MAX_WORKERS = 4


def _reg_workers() -> int:
    """One core is left for what the workers' tests spawn (servers, wrappers, children): REG's
    processes are never more than the cores minus one, at most REG_MAX_WORKERS (3 on CI's 4-vCPU
    runner, where 4 starved the sibling threads the race-shaped nodes below rely on)."""
    cores = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count()
    return max(1, min(REG_MAX_WORKERS, (cores or 2) - 1))


REG_WORKERS = _reg_workers()
REG_CHUNKS_PER_WORKER = 6  # whole-file chunks per worker: enough to balance, few enough to start
# Files a shared resource holds to the serial tail: none now (the HTTP smoke serves on `--port 0`
# and test_m6_extending builds over private plugin directories). A file added here runs whole,
# after the chunks.
REG_SERIAL_FILES: tuple[str, ...] = ()
# Timing-sensitive nodes: each passes alone and has failed on a loaded host.
REG_SERIAL_NODES = (
    "tests/test_mcp_stdio_smoke.py::test_stdio_run_returns_running_on_short_wait_ms",
    "tests/test_user_stories.py::test_us08_hostile_plugin_control_plane_responsive",
    "tests/core/spine/test_cs1_framing.py::test_e6_rerun_quartile_ratio",
    "tests/pins/c_surface/test_goldens.py::test_runview_shape_matches_golden",
    "tests/pins/c_surface/test_g_c4.py::test_pin_index_coarsened_summary_is_field_count",
    "tests/proof/spine/test_stop_offset.py::"
    "test_stop_seen_and_releases_past_the_offset_are_not_counted",
)
# Nodes that read the session's own proof results (`ledger.render()` over tests/proof/results):
# in the serial step each sees exactly the records of the nodes before it. Each runs alone after
# every other process, over a results dir holding just those records (`_seeded_run`), so it reads
# what the serial step would show it, never a concurrent process's records.
REG_RESULTS_READERS = (
    "tests/core/docs/test_cl_d1_deferrals.py::test_core_deferrals_cited_and_unproven",
)
# Each package's tests (collected by `pytest -q` through testpaths) run in one process given the
# package's testpaths root `packages/<name>/tests`, as the serial step reaches them: their modules
# import `conftest` and siblings by name, which a file-by-file argument list reorders. The packs
# root's process and the CSC-12 packs session share one worker, so the two packs runs never overlap.
REG_PACKAGES_PREFIX = "packages/"
COLLECTED_ENV = "TRESTLE_CK_REG_COLLECTED"
REG_PLUGIN = "tests.core.tooling.ck_drill"
_INI = ["-c", "pyproject.toml", "--rootdir", "."]


def pytest_collection_finish(session: Any) -> None:
    """Pytest hook, live only in the parallel REG's processes (`-p tests.core.tooling.ck_drill`):
    the node ids this process selected, after every deselection, written to $COLLECTED_ENV."""
    out = os.environ.get(COLLECTED_ENV)
    if out:
        Path(out).write_text(json.dumps([item.nodeid for item in session.items]))


@dataclass
class RegGroup:
    """One pytest process of the parallel REG: the paths it is given (files, node ids, or a
    package's testpaths root), the node ids it must select, and the nodes of its files that run
    in another group instead (deselected here)."""

    label: str
    paths: list[str] = field(default_factory=list)
    nodes: list[str] = field(default_factory=list)
    deselect: list[str] = field(default_factory=list)


@dataclass
class RegPlan:
    """The serial collection dealt into groups; every node is in exactly one."""

    chunks: list[RegGroup]  # whole-file chunks, largest first
    packages: list[RegGroup]  # one per package testpaths root
    tail: RegGroup  # the shared-resource files and the timing-sensitive nodes, serially
    readers: list[RegGroup]  # the results readers, one process each, after the tail


def _named(node: str, names: tuple[str, ...]) -> bool:
    return any(node == n or node.startswith(n + "[") for n in names)


def reg_partition(nodes: list[str], workers: int = REG_WORKERS) -> RegPlan:
    """The serial collection `nodes` dealt into a `RegPlan`."""
    by_file: dict[str, list[str]] = {}
    for node in nodes:
        by_file.setdefault(node.split("::", 1)[0], []).append(node)
    tail = RegGroup("pytest [serial tail]")
    packages: dict[str, RegGroup] = {}
    readers: dict[str, RegGroup] = {}
    files: list[RegGroup] = []
    for path, ids in by_file.items():
        if path in REG_SERIAL_FILES:
            tail.paths.append(path)
            tail.nodes += ids
            continue
        moved = [n for n in ids if _named(n, REG_SERIAL_NODES)]
        tail.paths += moved
        tail.nodes += moved
        for node in ids:
            if _named(node, REG_RESULTS_READERS):
                reader = readers.setdefault(node.split("[", 1)[0], RegGroup(""))
                reader.paths.append(node)
                reader.nodes.append(node)
                moved.append(node)
        kept = [n for n in ids if n not in moved]
        if path.startswith(REG_PACKAGES_PREFIX):
            # given its testpaths root, a package's process collects this file whatever is kept:
            # every node that runs elsewhere is deselected there, a file's last one included
            path = "/".join(path.split("/")[:3])
            group = packages.setdefault(path, RegGroup(f"pytest [{path}]"))
        elif not kept:
            continue
        else:
            group = RegGroup("")
            files.append(group)
        if path not in group.paths:
            group.paths.append(path)
        group.nodes += kept
        group.deselect += moved
    target = max(1, -(-sum(len(g.nodes) for g in files) // (workers * REG_CHUNKS_PER_WORKER)))
    chunks: list[RegGroup] = []
    for group in files:
        if not chunks or len(chunks[-1].nodes) >= target:
            chunks.append(RegGroup(""))
        chunks[-1].paths += group.paths
        chunks[-1].nodes += group.nodes
        chunks[-1].deselect += group.deselect
    chunks.sort(key=lambda g: len(g.nodes), reverse=True)
    for i, chunk in enumerate(chunks, 1):
        chunk.label = f"pytest [{i}/{len(chunks)}]"
    for name, reader in readers.items():
        reader.label = f"pytest [results reader {name}]"
    return RegPlan(chunks, list(packages.values()), tail, list(readers.values()))


def partition_problems(serial: list[str], ran: dict[str, list[str] | None]) -> list[str]:
    """CI's `ci_shards` proof for REG: every process recorded its collection, no node was
    selected by two processes, and their union is the serial collection exactly."""
    problems = [
        f"REG partition: {label} recorded no collection"
        for label, ids in ran.items()
        if ids is None
    ]
    seen: dict[str, str] = {}
    for label, ids in ran.items():
        for node in ids or []:
            if node in seen:
                problems.append(f"REG partition: {node} ran in {seen[node]} and {label}")
            seen[node] = label
    serial_set = set(serial)
    missing = [n for n in serial if n not in seen]
    extra = [n for n in seen if n not in serial_set]
    for what, ids in (("ran in no process", missing), ("is not in the serial collection", extra)):
        if ids:
            more = f" (and {len(ids) - 10} more)" if len(ids) > 10 else ""
            problems.append(f"REG partition: {len(ids)} node(s) {what}: {ids[:10]}{more}")
    return problems


def _run(root: Path, cmd: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=root, env=env, capture_output=True, text=True)


def registered_nodes(root: Path, ids: set[str], env: dict[str, str]) -> list[str]:
    """Node ids whose `proves()` markers name one of `ids` (the CK's registered nodes)."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "nodes.json"
        _run(
            root,
            [
                sys.executable,
                "-m",
                "pytest",
                "--collect-only",
                "-q",
                "-p",
                "tests.proof.audit_plugin",
            ]
            + ["-p", "no:cacheprovider"],
            dict(env, TRESTLE_AUDIT_OUT=str(out)),
        )
        nodes = json.loads(out.read_text())["nodes"] if out.exists() else []
    return [n["nodeid"] for n in nodes if ids & set(n["labels"])]


def _failure(label: str, proc: subprocess.CompletedProcess[str]) -> str | None:
    if proc.returncode == 0:
        return None
    tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-15:])
    return f"{label} exited {proc.returncode}:\n{tail}"


def _selecting(
    root: Path, cmd: list[str], env: dict[str, str], out: Path
) -> tuple[subprocess.CompletedProcess[str], list[str] | None]:
    """`cmd` run with the collection recorder: (its process, the node ids it selected)."""
    proc = _run(root, [*cmd, "-p", REG_PLUGIN], dict(env, **{COLLECTED_ENV: str(out)}))
    return proc, (json.loads(out.read_text()) if out.exists() else None)


def _parallel_reg(
    root: Path,
    serial: list[str],
    workers: int,
    pytest_cmd: list[str],
    deselect: list[str],
    gate_env: dict[str, str],
    packs: tuple[str, list[str], dict[str, str]],
    typecheck: list[tuple[str, list[str], dict[str, str]]],
    tmp: Path,
) -> list[str]:
    """REG's `pytest` step over the partition of its serial collection, the packs session and
    the typecheck: `workers` processes at a time, largest first, then the serial tail, then each
    results reader over the records the serial step would have shown it. Returns
    the failures in a fixed order (never completion order), the partition proof's last."""
    plan = reg_partition(serial, workers)
    ran: dict[str, list[str] | None] = {}

    def group_cmd(group: RegGroup) -> list[str]:
        return pytest_cmd + [f"--deselect={n}" for n in deselect + group.deselect] + group.paths

    def run_group(group: RegGroup, idx: int) -> list[str]:
        proc, ran[group.label] = _selecting(root, group_cmd(group), gate_env, tmp / f"g{idx}")
        failure = _failure(group.label, proc)
        return [failure] if failure else []

    def run_step(step: tuple[str, list[str], dict[str, str]]) -> list[str]:
        failure = _failure(step[0], _run(root, step[1], step[2]))
        return [failure] if failure else []

    session_root = packs[1][-1]  # the CSC-12 session's path: packages/trestle-packs/tests
    jobs: list[Callable[[], list[str]]] = []
    for i, group in enumerate(plan.packages):  # the packs root's process, then the packs session
        if group.paths == [session_root]:
            jobs.append(lambda g=group, i=i: run_group(g, -1 - i) + run_step(packs))
        else:
            jobs.append(lambda g=group, i=i: run_group(g, -1 - i))
    if not any(g.paths == [session_root] for g in plan.packages):
        jobs.append(lambda: run_step(packs))
    jobs += [lambda step=step: run_step(step) for step in typecheck]
    jobs += [lambda c=c, i=i: run_group(c, i) for i, c in enumerate(plan.chunks, 1)]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:  # FIFO: submission order
        outcomes = list(pool.map(lambda job: job(), jobs))
    failures = [f for fs in outcomes for f in fs]
    if plan.tail.nodes:
        failures += run_group(plan.tail, len(plan.chunks) + 1)
    results_dir = root / "tests" / "proof" / "results"
    for i, reader in enumerate(plan.readers, len(plan.chunks) + 2):
        before = set(serial[: min(serial.index(n) for n in reader.nodes)])
        failures += _seeded_run(results_dir, before, lambda r=reader, i=i: run_group(r, i))
    groups = [g for g in [*plan.packages, *plan.chunks, plan.tail, *plan.readers] if g.nodes]
    return failures + partition_problems(serial, {g.label: ran.get(g.label) for g in groups})


def _record_node(line: str) -> str | None:
    try:
        return str(json.loads(line).get("nodeid"))
    except (ValueError, AttributeError):
        return None


def _seeded_run(results_dir: Path, before: set[str], run: Callable[[], list[str]]) -> list[str]:
    """`run` over a results dir holding only the records of the `before` nodes (what the serial
    step had written when it reached the reader); then every record is put back, the run's own
    appended."""
    results_dir.mkdir(parents=True, exist_ok=True)
    held = {p.name: p.read_text().splitlines(keepends=True) for p in results_dir.glob("*.jsonl")}
    seeds = {name: [x for x in lines if _record_node(x) in before] for name, lines in held.items()}
    for name, lines in seeds.items():
        (results_dir / name).write_text("".join(lines))
    try:
        return run()
    finally:
        for path in results_dir.glob("*.jsonl"):
            new = path.read_text().splitlines(keepends=True)[len(seeds.get(path.name, [])) :]
            held.setdefault(path.name, []).extend(new)
        for name, lines in held.items():
            (results_dir / name).write_text("".join(lines))


def run_regression(
    root: Path, decline: dict[str, Any], workers: int = REG_WORKERS
) -> RegressionResult:
    """REG (CSC-12) over the patched tree in `root`, every node outside the CK's registered nodes
    passing, then the ledger render: the CK's labels and clauses are never PROVEN. The `pytest`
    step runs as proven partitions over `workers` processes (REG_WORKERS above); when its
    collection fails, REG's steps run serially, as written."""
    result = RegressionResult()
    # the scratch root first: spawned wrapper/child processes start with `python -P` and import
    # `trestle` from the path, so without it they would run the un-declined tree (the checkout's
    # editable install, or an inherited PYTHONPATH)
    env = dict(
        os.environ,
        PYTHONPATH=os.pathsep.join(
            [str(root), str(root / "packages" / "trestle-packs")]
            + ([os.environ["PYTHONPATH"]] if os.environ.get("PYTHONPATH") else [])
        ),
        **{NESTED_ENV: "1"},
    )
    env.pop("TRESTLE_PROOF_GATE", None)
    env.pop(COLLECTED_ENV, None)  # an enclosing REG process's recorder is never this REG's
    ids = set(decline.get("labels", [])) | set(decline.get("clauses", []))
    deselect = registered_nodes(root, ids, env) + list(REG_NOT_RUN)
    results_dir = root / "tests" / "proof" / "results"
    shutil.rmtree(results_dir, ignore_errors=True)
    gate_env = dict(env, TRESTLE_PROOF_GATE="ci-test", GITHUB_ACTIONS="true")
    py = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    serial_pytest = py + [f"--deselect={n}" for n in deselect]
    packs: tuple[str, list[str], dict[str, str]] = (
        "packs pytest",
        py + [*_INI, "packages/trestle-packs/tests"],
        gate_env,
    )
    lint = (
        "trestle tests packages/trestle-packs conftest.py "
        "scripts/smoke_packs.py scripts/demo_pack_workflows.py"
    )
    ruff: list[tuple[str, list[str], dict[str, str]]] = [
        ("ruff check", [sys.executable, "-m", "ruff", "check", *lint.split()], env),
        ("ruff format", [sys.executable, "-m", "ruff", "format", "--check", *lint.split()], env),
    ]
    typecheck: list[tuple[str, list[str], dict[str, str]]] = []
    ci = (root / ".github" / "workflows" / "ci.yml").read_text()
    for match in re.finditer(r"^\s*- run: (.*\bmypy\b.*)$", ci, re.M):
        cmd = match.group(1).strip().split()
        argv = [sys.executable, *cmd[1:]] if cmd[0] == "python" else cmd
        if argv[0] == "mypy":
            argv = [sys.executable, "-m", "mypy", *argv[1:]]
        typecheck.append(("typecheck", argv, env))
    with tempfile.TemporaryDirectory(prefix="ck-reg-") as tmp:
        proc, serial = _selecting(
            root, [*serial_pytest, "--collect-only"], gate_env, Path(tmp) / "s"
        )
        if proc.returncode != 0 or serial is None:
            # no clean collection to partition: REG's steps, serially, exactly as written
            for label, cmd, step_env in [
                ("pytest", serial_pytest, gate_env),
                packs,
                *ruff,
                *typecheck,
            ]:
                failure = _failure(label, _run(root, cmd, step_env))
                result.failures += [failure] if failure else []
        else:
            result.failures += _parallel_reg(
                root, serial, workers, py + _INI, deselect, gate_env, packs, typecheck, Path(tmp)
            )
            for label, cmd, step_env in ruff:  # after every test: it lints what the tests left
                failure = _failure(label, _run(root, cmd, step_env))
                result.failures += [failure] if failure else []
    code = (
        "import json; from tests.proof import ledger; "
        "print(json.dumps({k: v['status'] for k, v in ledger.render().items()}))"
    )
    proc = _run(root, [sys.executable, "-c", code], env)
    if proc.returncode != 0:
        result.failures.append(f"ledger render failed:\n{proc.stderr.strip()[-600:]}")
    else:
        statuses = json.loads(proc.stdout.strip().splitlines()[-1])
        result.proven = sorted(i for i in ids if statuses.get(i) == "PROVEN")
    return result


def variant_failures(root: Path, decline: dict[str, Any]) -> list[str]:
    """A switch-tied variant test that exists after the patch runs un-xfailed and passes."""
    failures = []
    env = dict(os.environ, **{NESTED_ENV: "1"})
    for node in decline.get("variants", []):
        if not (root / node.split("::")[0]).is_file():
            continue  # the patch reverted the file with the CK's other files (step 2)
        proc = _run(
            root,
            [sys.executable, "-m", "pytest", "-q", "--runxfail", "-p", "no:cacheprovider", node],
            env,
        )
        if proc.returncode != 0:
            failures.append(f"variant {node}: {(proc.stdout + proc.stderr).strip()[-300:]}")
    return failures


# ---------------------------------------------------------------------------
# the drill
# ---------------------------------------------------------------------------


@contextmanager
def scratch_worktree(repo: Path) -> Iterator[Path]:
    """A detached worktree at `repo`'s HEAD, removed on exit (the drill never edits master)."""
    parent = Path(tempfile.mkdtemp(prefix="ck-drill-"))
    path = parent / "scratch"
    _git(repo, "worktree", "add", "--detach", str(path), "HEAD")
    try:
        yield path
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(path)], cwd=repo)
        shutil.rmtree(parent, ignore_errors=True)


@dataclass
class DrillReport:
    patch: Patch
    touched: set[str]
    problems: list[str]


def drill(
    repo: Path,
    decline: dict[str, Any],
    *,
    lane_globs: list[str],
    span: Span | None = None,
    regression: Regression | None = None,
) -> DrillReport:
    """Compute `decline`'s patch at `repo`'s HEAD, apply it in a scratch worktree and check CM-7's
    scope rule and drill conditions (a) and (b). `regression` runs REG and the ledger render on
    the patched tree (`run_regression` in CI; a planted history has no suite to run)."""
    span = span or span_for(repo, decline["merge"])
    patch = derive_patch(repo, decline, lane_globs, span)
    problems = switch_isolation_violations(repo, decline, span, patch)  # (b), at HEAD
    with scratch_worktree(repo) as scratch:
        apply_patch(scratch, patch)
        touched = touched_paths(scratch)
        assert_within_scope(touched, patch.scope)
        problems += declined_state_violations(scratch, decline)  # (a), static half
        if regression is not None:
            outcome = regression(scratch, decline)
            problems += outcome.failures
            problems += [f"{i} renders PROVEN with the patch applied" for i in outcome.proven]
            problems += variant_failures(scratch, decline)
    return DrillReport(patch=patch, touched=touched, problems=problems)
