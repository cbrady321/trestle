"""CK-1's decline patch entry (MC-CORE-12; CM-7): K-1, the same idempotency key joins its run after
a republish and the key's window covers the run's life (OQ-1 recorded default).

The switch is `admission.JOIN_ACROSS_REPUBLISH`, read at every line the CK-1 merge added to a file
another lane may edit (admission.py: the join comparison and the window). Declining K-1 returns the
join to an equal snapshot id and the window to `ttl` from admission, which *is* OQ-1's conflict
variant: the TM-C4a node then runs un-xfailed and passes. The G-C3 target and its register entry
come back with the reverted pin file (TM-P0-2:G-C3 named-not-removed). The entry lists no file:
the files of the patch are CM-7's derived set."""

DECLINE = {
    "merge": "CK-1",
    "k": "K-1",
    "switch": {
        "module": "trestle.server.admission",
        "name": "JOIN_ACROSS_REPUBLISH",
        "declined": False,
    },
    "restores": ["TM-P0-2:G-C3"],
    "labels": [
        "WR-IDEM-1:join-after-republish",
        "WR-IDEM-1:window-covers-run-life",
        "WR-IDEM-1:answer-names-identity",
        "WR-PROOF-10:K-1",
    ],
    "clauses": ["A3.1"],
    "variants": ["tests/core/admission/test_ck1_join.py::test_variant_conflict"],
}
