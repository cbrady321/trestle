"""A deliberately failing test that no session may collect (kept out by `tests/conftest.py`).

`tests/unit/test_collection_scope.py` proves the ignore works by collecting this file by explicit
path (which the ignore does not cover) and by finding it in no default collection.
"""


def test_probe_fails_if_ever_collected() -> None:
    raise AssertionError("a test under packages/trestle-env/tests/fixtures/ was collected")
