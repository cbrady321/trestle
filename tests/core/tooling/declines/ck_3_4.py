"""The decline entry of CK-3/4 (MC-CORE-12): a maintainer "no" to K-3 (typed-record arguments)
and K-4's typed half. The switch is `trestle.plugin._codec.TYPED_RECORDS`; declining restores
dict delivery (S0), not the OQ-3 = refuse alternative, which would be its own later change.
`ck_drill` derives the patch from this entry and the merge's landing diff (CM-7); this entry
names no file."""

DECLINE = {
    "merge": "CK-3/4",
    "k": ["K-3", "K-4"],
    "switch": {"module": "trestle.plugin._codec", "name": "TYPED_RECORDS", "declined": False},
    "restores": ["T-3"],
    "labels": [
        "WR-PLAN-9:dataclass-arg-typed",
        "WR-PLAN-9:dataclass-return-succeeds",
        "WR-PLAN-9:variant-refuse-written",
        "WR-PROOF-10:K-3",
        "WR-PROOF-10:K-4",
    ],
}
