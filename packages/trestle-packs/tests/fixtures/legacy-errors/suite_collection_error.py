"""Fixture suite for L.NW-1.5: a module that fails at collection (import error)."""

import module_that_does_not_exist_for_the_collection_error_fixture  # noqa: F401


def test_never_collected():
    pass
