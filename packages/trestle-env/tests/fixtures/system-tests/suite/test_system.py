"""The reference system tests: an ERROR-ONLY suite (L.RB-5.2; WR-ENV-5).

Every test needs a fixture that fails at setup, so pytest reports errors and no failure and no
pass. A runner that read only `failed == 0` (or an exit status) would call this suite green; the
reference workflow must never pass it: `test_counts.errors > 0` is decisive (B4-C6, B4.3). Never
collected by an outer session (the env conftest ignores `fixtures/*`); the case copies it into a
project directory and runs it through the workflow."""

import pytest


@pytest.fixture
def system():
    raise RuntimeError("the system under test never came up")


def test_the_system_answers(system):
    raise AssertionError("never reached")


def test_the_system_is_consistent(system):
    raise AssertionError("never reached")
