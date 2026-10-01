"""The environment distribution (MC-B-02 scaffold; plan RB-0).

Import rule (root C.5 step 4, checked by `tests/proof/test_import_boundaries.py`): this package
imports only the standard library, `trestle.plugin`, `trestle.workflow` and itself; only the
composition roots in `plugins/*.py` bind concrete `trestle_packs` adapters.
"""
