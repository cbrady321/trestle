"""Stage-critic review records (RV-n; L.P0-0d.8, read by L.P0-0a.3's ledger).

A record lives at `tests/proof/reviews/<RV-n>-<ckpt>.toml` (CM-5 role 1,
e.g. `RV-5-j0.toml`), written by the stage critic from the main checkout:

    id = "RV-5"
    mode = "stage-critic review"
    outcome = "pass"                 # pass | fail
    sha = "<the commit it reviewed>" # R6-2
    transcribe_log = "<the paired transcribe --check output>"
    [criteria]
    verbatim_fragments = "pass"      # each criterion: pass | fail
    row_set = "pass"
    owner = "pass"
    k_docs = "pass"

Each review names its own criteria (C.4) and its paired automated check:
`CRITERIA` holds, per RV id, the exact criterion keys and the one log key the
record carries. RV-5 (transcription) keeps the four criteria above and
`transcribe_log`; RV-1, RV-3 and RV-4 (J-CORE, L.J-CORE.1) and RV-2 (the
STUB-PROVEN wording, L.CZ.9.fix1) carry `paired_check_log`, the output of
their automated presence check. The key sets
are closed: an RV id with no `CRITERIA` entry, an unknown or missing key, or
another review's criteria is a schema error. An `outcome = "pass"` record must
have every criterion `pass`. `load_valid` skips a malformed record (the ledger shows
`review:<id>` only for a valid passing one); `load` raises on it.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REVIEWS_DIR = ROOT / "tests" / "proof" / "reviews"

BASE_KEYS = {"id", "mode", "criteria", "outcome", "sha"}
CRITERIA_KEYS = ("verbatim_fragments", "row_set", "owner", "k_docs")  # RV-5
PAIRED_LOG = "paired_check_log"
# RV id -> (its exact criterion keys, its log key). The criteria are the ones each
# review's verdict names (plan-workflow-runtime.md C.4; plans/core.md L.J-CORE.1).
CRITERIA: dict[str, tuple[tuple[str, ...], str]] = {
    # WR-CANCEL-6 boundaries and the K-19 disclosure (paired: test_cl_d1_disclosures.py)
    "RV-1": (
        (
            "agents_md_attributable_scope",
            "plugins_md_double_fork",
            "security_md_pre_identity_run",
            "no_universal_claim",
        ),
        PAIRED_LOG,
    ),
    # WR-PROOF-3 "real tool unverified" wording on every STUB-PROVEN row (the L.RB-12.4 packet,
    # `tests/proof/b/rv2_packet.py`; paired: tests/proof/b/test_rv2_packet.py, which writes the
    # packet to tests/proof/results/rv2-packet-slice-b.md). Added at CZ (L.CZ.9.fix1) so that
    # J-ROOT's `RV-2-root.toml` can be a valid record.
    "RV-2": (
        (
            "every_stub_row_states_real_tool_unverified",
            "stub_rows_disclosed_in_docs",
            "twin_rows_defer_to_host_record",
            "no_real_tool_claim",
        ),
        PAIRED_LOG,
    ),
    # WR-AUTH-5 "not a sandbox" and the closed policy sets (paired: test_cl_a2_profile.py)
    "RV-3": (
        (
            "no_sandbox_claim",
            "full_profile_ten_tools",
            "closed_sets_no_destructive",
            "no_foreign_cleanup",
            "profile_operator_only",
        ),
        PAIRED_LOG,
    ),
    # R-J (packages recorded, not snapshotted) and K-2/K-5/K-9 docs (paired: test_cl_c1_docs.py)
    "RV-4": (
        (
            "rj_statement_present",
            "rj_matches_code",
            "k2_k5_k9_docs_accurate",
            "captured_vs_not_stated",
        ),
        PAIRED_LOG,
    ),
    # matrix-map and row-owner transcription (paired: transcribe --check all)
    "RV-5": (CRITERIA_KEYS, "transcribe_log"),
}
MODE = "stage-critic review"
VERDICTS = ("pass", "fail")

_ID_RE = re.compile(r"^RV-\d+$")
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
_NAME_RE = re.compile(r"^(RV-\d+)-[a-z0-9-]+\.toml$")


class ReviewSchemaError(ValueError):
    pass


def validate(record: dict, filename: str | None = None) -> None:
    rid = record.get("id")
    if not isinstance(rid, str) or not _ID_RE.match(rid):
        raise ReviewSchemaError(f"id {rid!r} is not RV-<n>")
    if rid not in CRITERIA:
        raise ReviewSchemaError(f"no criteria are defined for {rid} (reviews.CRITERIA)")
    criteria_keys, log_key = CRITERIA[rid]
    keys = BASE_KEYS | {log_key}
    extra = set(record) - keys
    missing = keys - set(record)
    if extra or missing:
        raise ReviewSchemaError(f"bad review schema (extra={extra}, missing={missing})")
    if record["mode"] != MODE:
        raise ReviewSchemaError(f"mode must be {MODE!r}")
    criteria = record["criteria"]
    if not isinstance(criteria, dict) or set(criteria) != set(criteria_keys):
        raise ReviewSchemaError(f"{rid} criteria keys must be exactly {criteria_keys}")
    for name, verdict in criteria.items():
        if verdict not in VERDICTS:
            raise ReviewSchemaError(f"criterion {name!r} must be one of {VERDICTS}")
    if record["outcome"] not in VERDICTS:
        raise ReviewSchemaError(f"outcome must be one of {VERDICTS}")
    if record["outcome"] == "pass" and any(v != "pass" for v in criteria.values()):
        raise ReviewSchemaError("outcome pass with a failing criterion")
    if not isinstance(record["sha"], str) or not _SHA_RE.match(record["sha"]):
        raise ReviewSchemaError("sha must be a 7-40 char lowercase hex commit id")
    log = record[log_key]
    if not isinstance(log, str) or not log.strip():
        raise ReviewSchemaError(f"{log_key} must be a non-empty string")
    if filename is not None:
        match = _NAME_RE.match(filename)
        if not match or match.group(1) != rid:
            raise ReviewSchemaError(f"file name {filename!r} does not match id {rid!r}")


def load(path: Path) -> dict:
    """Parse and validate one record; raises `ReviewSchemaError`."""
    try:
        record = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ReviewSchemaError(f"{path.name}: unreadable ({exc})") from exc
    validate(record, path.name)
    return record


def load_valid(reviews_dir: Path | None = None) -> list[dict]:
    """Every valid record under `reviews_dir`; malformed ones are skipped."""
    directory = REVIEWS_DIR if reviews_dir is None else reviews_dir
    if not directory.exists():
        return []
    records = []
    for path in sorted(directory.glob("*.toml")):
        try:
            records.append(load(path))
        except ReviewSchemaError:
            continue
    return records
