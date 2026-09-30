"""`python -m tests.proof.differ` (CSC-5, MC-06): the divergence engine.

`L.P0-0c.4` builds the engine and mode `d1` (golden-S0, bidirectional,
in-process facet diff). `d2` (backward straddle, cross-worktree) is
`L.P0-0c.7`. Every other mode is registered here as "not built" and exits 2
naming its own future builder leaf, so an early caller of an unbuilt mode
fails loudly rather than silently no-op'ing.
"""

from __future__ import annotations

import argparse
import fnmatch
import importlib
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from tests.proof import normalize as normalize_mod

ROOT = Path(__file__).resolve().parents[2]
FACETS_PATH = ROOT / "tests" / "proof" / "facets.toml"
DIVERGENCE_PATH = ROOT / "tests" / "proof" / "divergence.toml"
D2_EXCEPTIONS_PATH = ROOT / "tests" / "proof" / "d2_exceptions.toml"
D2_DRIVER_PATH = ROOT / "tests" / "proof" / "d2_driver.py"
FOSSILS_ROOT_DEFAULT = ROOT / "tests" / "fixtures" / "fossils"

# CM-9's exact schema: [[exception]] {id, reader, states, declared_by, note?}.
# `retires_at`, `scope`, `register_id` are withdrawn keys (CM-12) and a load
# error if present.
CM9_REQUIRED_KEYS = {"id", "reader", "states", "declared_by"}
CM9_OPTIONAL_KEYS = {"note"}
CM9_ALL_KEYS = CM9_REQUIRED_KEYS | CM9_OPTIONAL_KEYS

# Modes this delivery (P0) does not build. Each names its own CSC-5 builder
# leaf (plan-workflow-runtime.md's differ mode table); `d1`'s closure mode
# is `L.CZ.4`, listed for completeness though it never appears as a CLI
# argument today (there is no `differ.py cz4` subcommand to close).
UNBUILT_MODES = {
    "d3": "L.CS-2.4",
    "d6": "L.TR-6.1",
}


def load_facets() -> list[dict[str, object]]:
    return list(tomllib.loads(FACETS_PATH.read_text()).get("facet", []))


def load_divergence() -> list[dict[str, object]]:
    if not DIVERGENCE_PATH.exists():
        return []
    return list(tomllib.loads(DIVERGENCE_PATH.read_text()).get("entry", []))


CODES_FACET = "refusal_codes"


def permitted_additive_codes() -> list[str]:
    """Wire-value patterns (fnmatch) of refusal/projection codes the ledger
    names as deliberate additive divergences: every entry with facet
    `refusal_codes`, direction `additive`, and a `codes` list. One entry per
    code (or per plan-named family such as `execution.*`), citing its row."""
    patterns: list[str] = []
    for entry in load_divergence():
        if entry.get("facet") == CODES_FACET and entry.get("direction") == "additive":
            patterns.extend(str(c) for c in entry.get("codes", []))
    return patterns


def drop_named_additive_codes(unexpected: list[str], current: object) -> list[str]:
    """Remove from a refusal_codes `unexpected` list the `$.codes.<NAME>`
    paths whose wire value is named by `permitted_additive_codes()`. Nothing
    else is excused; `missing` (a removed or renamed S0 code) is never
    filtered."""
    codes = current.get("codes", {}) if isinstance(current, dict) else {}
    patterns = permitted_additive_codes()
    kept = []
    for path in unexpected:
        name = path.removeprefix("$.codes.") if path.startswith("$.codes.") else None
        value = codes.get(name) if name is not None else None
        if isinstance(value, str) and any(fnmatch.fnmatch(value, p) for p in patterns):
            continue
        kept.append(path)
    return kept


# Named additive divergences of a facet's shape (L.P0-0d.20). An entry with
# `facet = <a d1 facet>` and `direction = "additive"` may carry selectors, each
# naming exactly one kind of growth of the S0 golden:
#   keys  = ["$.frames.*.cleanup"]                              a dict key added
#   items = [{ path = "$.sequences.*", value = "group_stop" }]  a list item
#           inserted: the golden list must then be an ordered subsequence of
#           the current list, and every extra item must equal a named `value`
#   grows = [{ path = "$.tools_bytes", max = 8192 }]            an int that
#           may grow, never shrink, up to `max`
# A `path` uses the diff's own `$.a.b[0]` spelling; `*` matches one key
# segment (no `.` or `[`), everything else is literal. Nothing else is ever
# excused: a removed or renamed key, a changed value or type, a reordered
# list, an unnamed insertion (even on a "free" facet's aligned list) all fail.
# A selector that excuses nothing on the current head is stale and fails d1.
# An additive entry without selectors (the MC-06 seed placeholders) excuses
# nothing.
SELECTOR_FIELDS = ("keys", "items", "grows")


@dataclass(frozen=True)
class Selector:
    entry_id: str
    facet: str
    kind: str  # "key" | "item" | "grow"
    path: str
    value: str = ""  # canonical JSON of an item's value
    max: int = 0

    def describe(self) -> str:
        if self.kind == "item":
            return f"item {self.path} value={self.value}"
        if self.kind == "grow":
            return f"grow {self.path} max={self.max}"
        return f"key {self.path}"


@dataclass
class FacetDiff:
    missing: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)
    used: set[Selector] = field(default_factory=set)


def _canon(value: object) -> str:
    """Type-exact equality key: `1`, `1.0` and `true` stay distinct."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _path_matches(pattern: str, path: str) -> bool:
    regex = "[^.\\[]+".join(re.escape(part) for part in pattern.split("*"))
    return re.fullmatch(regex, path) is not None


def entry_selectors(entry: dict[str, object]) -> list[Selector]:
    """The selectors one divergence entry carries, validated. Raises
    ValueError on a malformed selector, or on selectors in an entry that is
    not `direction = "additive"` or that is a `refusal_codes` entry."""
    if not any(k in entry for k in SELECTOR_FIELDS):
        return []
    eid, facet = str(entry.get("id")), str(entry.get("facet"))
    if entry.get("direction") != "additive":
        raise ValueError(f'{eid}: selectors need direction = "additive"')
    if facet == CODES_FACET:
        raise ValueError(f"{eid}: a refusal_codes entry names `codes`, not selectors")
    keys, items, grows = (entry.get(k, []) for k in SELECTOR_FIELDS)
    if not (isinstance(keys, list) and isinstance(items, list) and isinstance(grows, list)):
        raise ValueError(f"{eid}: keys/items/grows must be arrays")
    out: list[Selector] = []
    for key in keys:
        if not (isinstance(key, str) and key.startswith("$.")):
            raise ValueError(f"{eid}: bad keys selector {key!r}")
        out.append(Selector(eid, facet, "key", key))
    for item in items:
        if not (isinstance(item, dict) and set(item) == {"path", "value"}):
            raise ValueError(f"{eid}: an items selector is exactly {{path, value}}: {item!r}")
        value = _canon(normalize_mod.normalize(item["value"]))
        out.append(Selector(eid, facet, "item", str(item["path"]), value=value))
    for grow in grows:
        if not (isinstance(grow, dict) and set(grow) == {"path", "max"}):
            raise ValueError(f"{eid}: a grows selector is exactly {{path, max}}: {grow!r}")
        if type(grow["max"]) is not int:
            raise ValueError(f"{eid}: a grows selector's max is an int: {grow!r}")
        out.append(Selector(eid, facet, "grow", str(grow["path"]), max=grow["max"]))
    return out


def facet_selectors(fid: str, entries: list[dict[str, object]]) -> list[Selector]:
    return [s for e in entries if e.get("facet") == fid for s in entry_selectors(e)]


def named_diff(
    golden: object, current: object, *, policy: str, selectors: list[Selector]
) -> FacetDiff:
    """`normalize.structural_diff` with a facet's named additive selectors
    applied (both values already `normalize()`d). `unexpected` honours
    `policy` ("free" drops unnamed new keys and trailing positional items),
    except that an unnamed item in a list aligned by an `items` selector
    fails on both policies."""
    out = FacetDiff()
    strict: list[str] = []
    _named(golden, current, "$", selectors, out, strict)
    if policy == "free":
        out.unexpected = []
    out.unexpected += strict
    return out


def _named(
    golden: object,
    current: object,
    path: str,
    sels: list[Selector],
    out: FacetDiff,
    strict: list[str],
) -> None:
    if isinstance(golden, dict) and isinstance(current, dict):
        for key in golden:
            if key not in current:
                out.missing.append(f"{path}.{key}")
            else:
                _named(golden[key], current[key], f"{path}.{key}", sels, out, strict)
        for key in current:
            if key in golden:
                continue
            child = f"{path}.{key}"
            hit = next((s for s in sels if s.kind == "key" and _path_matches(s.path, child)), None)
            if hit is not None:
                out.used.add(hit)
            else:
                out.unexpected.append(child)
        return
    if isinstance(golden, list) and isinstance(current, list):
        item_sels = [s for s in sels if s.kind == "item" and _path_matches(s.path, path)]
        if not item_sels:
            for i, item in enumerate(golden):
                if i >= len(current):
                    out.missing.append(f"{path}[{i}]")
                else:
                    _named(item, current[i], f"{path}[{i}]", sels, out, strict)
            out.unexpected += [f"{path}[{i}]" for i in range(len(golden), len(current))]
            return
        # The golden list must be an ordered subsequence of the current one.
        # Greedy earliest matching is optimal: a current item equal to the
        # next golden item is never better left as an extra, because any
        # later equal item it would be traded for carries the same name.
        j = 0
        for item in current:
            canon = _canon(item)
            if j < len(golden) and canon == _canon(golden[j]):
                j += 1
                continue
            hit = next((s for s in item_sels if s.value == canon), None)
            if hit is not None:
                out.used.add(hit)
            else:
                strict.append(f"{path}[+{canon}]")
        out.missing += [f"{path}[{k}]" for k in range(j, len(golden))]
        return
    if type(golden) is type(current) and golden == current:
        return
    grow = next((s for s in sels if s.kind == "grow" and _path_matches(s.path, path)), None)
    if (
        grow is not None
        and type(golden) is int
        and type(current) is int
        and golden < current <= grow.max
    ):
        out.used.add(grow)
        return
    out.missing.append(path)


def facet_diff(
    fid: str,
    golden: object,
    current: object,
    *,
    policy: str,
    entries: list[dict[str, object]] | None = None,
) -> FacetDiff:
    """The one d1 comparison of a facet (the C-surface golden tests use it
    too): the facet's named additive selectors, plus the named additive
    codes for `refusal_codes`."""
    entries = load_divergence() if entries is None else entries
    result = named_diff(golden, current, policy=policy, selectors=facet_selectors(fid, entries))
    if fid == CODES_FACET:
        result.unexpected = drop_named_additive_codes(result.unexpected, current)
    return result


def stale_selectors(
    fids: set[str], used: set[Selector], entries: list[dict[str, object]]
) -> list[Selector]:
    """Selectors of the diffed facets that excused nothing: the entry names
    an addition the current head does not have."""
    return [
        s for e in entries if e.get("facet") in fids for s in entry_selectors(e) if s not in used
    ]


def _bad_entries(entries: list[dict[str, object]]) -> list[str]:
    bad = []
    for entry in entries:
        try:
            entry_selectors(entry)
        except ValueError as exc:
            bad.append(str(exc))
    return bad


def _resolve_extractor(spec: str):
    module_name, func_name = spec.split(":")
    module = importlib.import_module(module_name)
    return getattr(module, func_name)


def _due(entry: dict[str, object]) -> bool:
    """An entry is "due" once its `due_checkpoint` is an ancestor of HEAD.
    P0 has no checkpoint history yet (J0 has not happened): every P0-court
    entry is treated as due immediately (checkpoint "P0" or unset), which is
    the conservative reading — a missing diff for a P0-due entry fails now
    rather than silently later."""
    due_checkpoint = entry.get("due_checkpoint")
    return due_checkpoint in (None, "", "P0", "s0")


def cmd_d1(args: argparse.Namespace) -> int:
    facets = load_facets()
    if args.facet:
        wanted = set(args.facet)
        facets = [f for f in facets if f["id"] in wanted]

    ok = True
    diffed_facets: set[str] = set()
    used: set[Selector] = set()
    entries = load_divergence()
    bad = _bad_entries(entries)
    for problem in bad:
        eid, _, reason = problem.partition(": ")
        print(f"d1: BAD DIVERGENCE ENTRY: divergence entry {eid} ({reason})")
    if bad:
        return 1
    for facet in facets:
        fid = facet["id"]
        extractor = facet.get("extractor", "pending")
        if extractor == "pending":
            if args.strict:
                print(f"d1: STRICT: facet {fid!r} has no extractor yet (pending)")
                ok = False
            continue

        diffed_facets.add(fid)
        func = _resolve_extractor(str(extractor))
        current = normalize_mod.normalize(func())
        golden_path = ROOT / str(facet["golden"])
        if not golden_path.exists():
            print(f"d1: UNEXPECTED: facet {fid!r} has no golden file {golden_path}")
            ok = False
            continue
        golden = normalize_mod.normalize(json.loads(golden_path.read_text()))
        result = facet_diff(
            str(fid), golden, current, policy=str(facet.get("additive", "named")), entries=entries
        )
        used |= result.used
        if result.missing or result.unexpected:
            print(
                f"d1: UNEXPECTED DIFF: facet {fid!r}: "
                f"missing={result.missing} unexpected={result.unexpected}"
            )
            ok = False

    for sel in stale_selectors(diffed_facets, used, entries):
        print(
            f"d1: STALE DIVERGENCE: divergence entry {sel.entry_id} (facet {sel.facet!r}): "
            f"{sel.describe()} names no addition on this head"
        )
        ok = False

    for entry in entries:
        if entry.get("facet") not in {f["id"] for f in facets}:
            continue
        if _due(entry) and entry.get("facet") not in diffed_facets:
            print(
                f"d1: MISSING DUE DIFF: divergence entry {entry.get('id')} ({entry.get('facet')})"
            )
            ok = False

    return 0 if ok else 1


def _load_exceptions(path: Path) -> list[dict[str, object]]:
    """CM-9 named exceptions (L.P0-0c.7). Not a register entry (CM-7):
    nothing here ever retires, and the withdrawn keys (CM-12) are a load
    error, never silently ignored."""
    if not path.exists():
        return []
    exceptions = list(tomllib.loads(path.read_text()).get("exception", []))
    for exc in exceptions:
        extra = set(exc) - CM9_ALL_KEYS
        missing = CM9_REQUIRED_KEYS - set(exc)
        if extra or missing:
            raise ValueError(
                f"d2_exceptions.toml: {exc.get('id')}: bad schema "
                f"(extra={extra}, missing={missing})"
            )
    return exceptions


def _excused(exceptions: list[dict[str, object]], reader: str, state_path: str) -> bool:
    """An exception excuses a divergence only in its `states` globs and only
    when `differ d2` runs with exactly its `reader` (CM-9)."""
    for exc in exceptions:
        if exc.get("reader") != reader:
            continue
        for pattern in exc.get("states", []):
            if fnmatch.fnmatch(state_path, str(pattern)):
                return True
    return False


def band_awaits_checkpoint(band: str, cwd: Path = ROOT) -> bool:
    """True while the checkpoint that generates `band`'s fossil directories
    (`J-<BAND>`, e.g. `J-SINGLE` for `single/`; CM-5) has not succeeded at
    `cwd`'s HEAD: no `WR-Merge: J-<BAND>` carrier yet, or the newest carrier
    lacks its success mark (`fence.ckpt_succeeded`). A band with no
    checkpoint of its own (`s0/`) never awaits one."""
    from tests.proof import fence

    name = f"J-{band.upper()}"
    if name not in fence.CKPT_SUCCESS_MARK:
        return False
    return not fence.ckpt_succeeded(name, "HEAD", cwd=cwd)


def cmd_d2(args: argparse.Namespace) -> int:
    """`python -m tests.proof.differ d2 --reader <git ref|s0> [--fossils <dir>]`
    (L.P0-0c.7): backward straddle — an older reader (any git ref; `s0` is
    an alias for `5fbdd2f`) reads today's committed fossil corpus through
    `d2_driver.py`, run in a subprocess with that reader worktree (and its
    `packages/trestle-packs`) first on `PYTHONPATH`, never installed."""
    from tests.proof import fossils as fossils_mod

    reader = args.reader
    ref = "5fbdd2f" if reader == "s0" else reader
    fossils_root = Path(args.fossils) if args.fossils else FOSSILS_ROOT_DEFAULT

    states = fossils_mod.load_states(fossils_root)
    to_check: dict[str, str] = {}
    awaiting: dict[str, bool] = {}
    for state_id, (band, entry) in states.items():
        if entry.get("producer") in (None, "pending") or entry.get("absent"):
            continue
        state_dir = fossils_root / band / state_id
        # A band's state directories are generated by its checkpoint merge (CM-5; e.g.
        # L.J-SINGLE.1 for `single/`), after its MANIFEST already names real producers. An
        # absent directory is skipped only until that checkpoint has succeeded; afterwards
        # it is checked (and fails as projecting nothing).
        if not state_dir.exists():
            if band not in awaiting:
                awaiting[band] = band_awaits_checkpoint(band)
            if awaiting[band]:
                print(
                    f"d2: skip {band}/{state_id}: no state directory yet and "
                    f"J-{band.upper()} has not succeeded"
                )
                continue
        to_check[state_id] = str(state_dir / "home")

    if not to_check:
        print("d2: no fossil states with a real producer to check")
        return 0

    with tempfile.TemporaryDirectory() as tmp:
        worktree = Path(tmp) / "reader"
        add = subprocess.run(
            ["git", "worktree", "add", "--detach", str(worktree), ref],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if add.returncode != 0:
            print(f"d2: git worktree add failed for ref {ref!r}: {add.stderr}")
            return 1
        try:
            states_json = Path(tmp) / "states.json"
            states_json.write_text(json.dumps(to_check))
            env = dict(os.environ)
            env["PYTHONPATH"] = f"{worktree}{os.pathsep}{worktree / 'packages' / 'trestle-packs'}"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(D2_DRIVER_PATH),
                    "--reader-root",
                    str(worktree),
                    "--states-json",
                    str(states_json),
                ],
                capture_output=True,
                text=True,
                env=env,
            )
            if proc.returncode != 0:
                print(f"d2: driver failed (reader={reader}): {proc.stderr}")
                return 1
            report_lines = [line for line in proc.stdout.splitlines() if line.strip()]
            report = json.loads(report_lines[-1]) if report_lines else {}
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(worktree)],
                cwd=ROOT,
                capture_output=True,
            )

    exceptions = _load_exceptions(D2_EXCEPTIONS_PATH)
    ok = True
    for state_id, result in report.items():
        band, _entry = states[state_id]
        # The MANIFEST's declared `s0_projection.state` (what the S0 reader
        # projects after recovery); a state without one expects its own id.
        projection = states[state_id][1].get("s0_projection") or {}
        expected = projection.get("state", state_id)
        actual = result.get("projected_state")
        if actual != expected:
            state_path = f"{band}/{state_id}"
            if _excused(exceptions, reader, state_path):
                continue
            print(
                f"d2: UNEXPECTED: state {state_path!r} projected {actual!r}, "
                f"expected {expected!r} (reader={reader})"
            )
            ok = False

    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tests.proof.differ")
    sub = parser.add_subparsers(dest="mode", required=True)

    d1 = sub.add_parser("d1")
    d1.add_argument("--strict", action="store_true")
    d1.add_argument("--facet", action="append", default=[])

    d2 = sub.add_parser("d2")
    d2.add_argument("--reader", required=True)
    d2.add_argument("--fossils", default=None)

    d4 = sub.add_parser("d4")
    d4.add_argument("--fossils", default=None)

    d5 = sub.add_parser("d5")
    d5.add_argument("--fossils", default=None)

    d7 = sub.add_parser("d7")
    d7.add_argument("--answers", default=None)

    d8 = sub.add_parser("d8")
    d8.add_argument("--pair", default=None)

    for mode in UNBUILT_MODES:
        sub.add_parser(mode)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.mode == "d1":
        return cmd_d1(args)
    if args.mode == "d2":
        return cmd_d2(args)
    if args.mode == "d4":
        from tests.proof.differ_modes import d4_implicit_plan

        return d4_implicit_plan.main(args)
    if args.mode == "d5":
        from tests.proof.differ_modes import d5_one_key

        return d5_one_key.main(args)
    if args.mode == "d7":
        from tests.proof.differ_modes import d7_permutation_depth

        return d7_permutation_depth.main(args)
    if args.mode == "d8":
        from tests.proof.differ_modes import d8_fake_real

        return d8_fake_real.main(args)
    if args.mode in UNBUILT_MODES:
        print(f"differ {args.mode}: not built in P0; builder is {UNBUILT_MODES[args.mode]}")
        return 2
    parser.error(f"unknown mode {args.mode}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
