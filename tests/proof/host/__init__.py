"""HOST-only proof tooling (CSC-9/CSC-12; L.P0-0d.9): the `host_only`/
`docker_host` deselection hook lives in `tests.proof.plugin`; this package
holds the HOST lock (`host_lock.py`) and, from `L.P0-0d.3`/`L.P0-0d.6` on,
the record schema/selection (`record.py`) and the `proc_gate`/
`docker_gate` runners.
"""
