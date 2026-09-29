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

The key sets are closed. An `outcome = "pass"` record must have every
criterion `pass`. `load_valid` skips a malformed record (the ledger shows
`review:<id>` only for a valid passing one); `load` raises on it.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REVIEWS_DIR = ROOT / "tests" / "proof" / "reviews"

RECORD_KEYS = {"id", "mode", "criteria", "outcome", "sha", "transcribe_log"}
CRITERIA_KEYS = ("verbatim_fragments", "row_set", "owner", "k_docs")
MODE = "stage-critic review"
VERDICTS = ("pass", "fail")

_ID_RE = re.compile(r"^RV-\d+$")
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
_NAME_RE = re.compile(r"^(RV-\d+)-[a-z0-9-]+\.toml$")


class ReviewSchemaError(ValueError):
    pass


def validate(record: dict, filename: str | None = None) -> None:
    extra = set(record) - RECORD_KEYS
    missing = RECORD_KEYS - set(record)
    if extra or missing:
        raise ReviewSchemaError(f"bad review schema (extra={extra}, missing={missing})")
    if not isinstance(record["id"], str) or not _ID_RE.match(record["id"]):
        raise ReviewSchemaError(f"id {record['id']!r} is not RV-<n>")
    if record["mode"] != MODE:
        raise ReviewSchemaError(f"mode must be {MODE!r}")
    criteria = record["criteria"]
    if not isinstance(criteria, dict) or set(criteria) != set(CRITERIA_KEYS):
        raise ReviewSchemaError(f"criteria keys must be exactly {CRITERIA_KEYS}")
    for name, verdict in criteria.items():
        if verdict not in VERDICTS:
            raise ReviewSchemaError(f"criterion {name!r} must be one of {VERDICTS}")
    if record["outcome"] not in VERDICTS:
        raise ReviewSchemaError(f"outcome must be one of {VERDICTS}")
    if record["outcome"] == "pass" and any(v != "pass" for v in criteria.values()):
        raise ReviewSchemaError("outcome pass with a failing criterion")
    if not isinstance(record["sha"], str) or not _SHA_RE.match(record["sha"]):
        raise ReviewSchemaError("sha must be a 7-40 char lowercase hex commit id")
    log = record["transcribe_log"]
    if not isinstance(log, str) or not log.strip():
        raise ReviewSchemaError("transcribe_log must be a non-empty string")
    if filename is not None:
        match = _NAME_RE.match(filename)
        if not match or match.group(1) != record["id"]:
            raise ReviewSchemaError(f"file name {filename!r} does not match id {record['id']!r}")


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
