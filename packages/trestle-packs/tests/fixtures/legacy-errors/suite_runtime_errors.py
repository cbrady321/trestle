"""Fixture suite for L.NW-1.5: one setup error, one teardown error (its call passes).

Named `suite_*.py` so no `test_*.py` collection pattern picks it up; the leaf's tests run it
only by explicit path, in a subprocess."""

import pytest


@pytest.fixture
def broken_setup():
    raise RuntimeError("setup boom")


@pytest.fixture
def broken_teardown():
    yield
    raise RuntimeError("teardown boom")


def test_setup_error(broken_setup):
    pass


def test_teardown_error(broken_teardown):
    pass
