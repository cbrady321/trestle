"""CK-14's decline entry (MC-CORE-12, L.CK-14.1): a maintainer "no" to K-14 (mypy at zero).

K-14 has no module constant: its switch is the `typecheck` job's command, `mypy`, which the
decline patch turns back into P0's ratchet (`ck_drill` flips it in the CK merge's own files,
reverts the files no later commit changed, drops the `K-14` doc block, restores TM-P0-1
named-not-removed and sets the two labels na). A K-14 "no" contradicts frozen SC-2 (`mypy` = 0),
so this patch is applied only with the user's approval of that SC-2 change, never unattended
(C.9, PC4-4). The entry lists no file."""

DECLINE = {
    "merge": "CK-14",
    "k": "K-14",
    "switch": {
        "command": {"from": "mypy", "to": "python -m tests.proof.meta mypy-ratchet --max 1"}
    },
    "restores": ["TM-P0-1"],
    "labels": ["WR-PROOF-8:mypy-zero-3.12", "WR-PROOF-10:K-14"],
}
