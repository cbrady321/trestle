"""L.CL-C2.1 (BFD-16, WR-PLAN-8): a naive or malformed date/date-time is
refused `admission.invalid_args` at admission, before any run id exists."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from tests.proof import mcp_host
from trestle.common import codes
from trestle.server.plugin_schema import ArgsError, validate_args

_PLUGIN = textwrap.dedent(
    """
    from __future__ import annotations

    from datetime import date, datetime

    from trestle.plugin.surface import Context, trestle


    @trestle
    def when(ctx: Context, at: datetime, day: date | None = None) -> dict[str, str]:
        return {"at": str(at), "day": str(day)}
    """
)

_DATETIME = {"type": "string", "format": "date-time"}
_DATE = {"type": "string", "format": "date"}


def _run(host: mcp_host.McpHost, args: dict[str, object]) -> dict[str, object]:
    result = host.call("run", {"plugin": "when", "args": args, "wait_ms": 0})
    assert isinstance(result, dict), result
    return result


@pytest.mark.proves(
    "WR-PLAN-8",
    "WR-PLAN-8:naive-or-malformed-no-run-id",
    "core",
    "core",
    "must",
    "CI",
)
def test_naive_and_malformed_datetime_refused_via_mcp(tmp_path: Path) -> None:
    with mcp_host.McpHost(home=tmp_path / "home") as host:
        (host.home / "plugins" / "when.py").write_text(_PLUGIN, encoding="utf-8")
        for args in (
            {"at": "2026-01-01T00:00:00"},  # naive date-time
            {"at": "2026-13-40"},  # malformed
            {"at": "2026-01-01T00:00:00Z", "day": "2026-13-40"},  # malformed date
            {"at": "2026-01-01T00:00:00Z", "day": "2026-01-01T00:00:00"},  # not a date
        ):
            refused = _run(host, args)
            assert refused.get("code") == codes.INVALID_ARGS, (args, refused)
            assert "run_id" not in refused, (args, refused)
        # the same plugin admits aware values: the refusal is not blanket
        admitted = _run(host, {"at": "2026-01-01T00:00:00+02:00", "day": "2026-01-01"})
        assert admitted.get("code") != codes.INVALID_ARGS, admitted
        assert "run_id" in admitted, admitted


@pytest.mark.parametrize(
    "text",
    [
        "2026-01-01T00:00:00Z",
        "2026-01-01T00:00:00+00:00",
        "2026-01-01T12:30:45.123456-05:30",
    ],
)
def test_aware_datetime_admitted(text: str) -> None:
    validate_args({"at": text}, _obj({"at": _DATETIME}))


@pytest.mark.parametrize(
    "text",
    ["2026-01-01T00:00:00", "2026-01-01", "2026-13-40", "", "yesterday", "2026-01-01T25:00:00Z"],
)
def test_naive_or_malformed_datetime_refused(text: str) -> None:
    with pytest.raises(ArgsError):
        validate_args({"at": text}, _obj({"at": _DATETIME}))


@pytest.mark.parametrize(
    ("text", "ok"),
    [
        ("2026-01-01", True),
        ("2028-02-29", True),
        ("2026-02-29", False),
        ("2026-13-40", False),
        ("2026-01-01T00:00:00", False),
        ("", False),
    ],
)
def test_date_requires_valid_iso_date(text: str, ok: bool) -> None:
    schema = _obj({"day": _DATE})
    if ok:
        validate_args({"day": text}, schema)
    else:
        with pytest.raises(ArgsError):
            validate_args({"day": text}, schema)


def test_optional_datetime_none_still_admitted() -> None:
    schema = _obj({"at": {"anyOf": [_DATETIME, {"type": "null"}]}})
    validate_args({"at": None}, schema)
    with pytest.raises(ArgsError):
        validate_args({"at": "2026-01-01T00:00:00"}, schema)


def _obj(properties: dict[str, dict[str, object]]) -> dict[str, object]:
    return {"type": "object", "properties": properties, "additionalProperties": False}
