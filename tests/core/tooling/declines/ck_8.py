"""CK-8's decline patch entry (MC-CORE-12; CM-7): K-8, a succeeded run's processes are stopped.

The switch is `conductor.REAP_ON_SUCCESS`, read at the one call the CK-8 merge changed. A K-8 "no"
contradicts B2-C10, which kills on every terminal path, so this entry is applied only after the
interface owner amends B2-C10 (routed F-PLAN-6), never as an unattended step (plan core.md, CK-8).
The entry lists no file: the files of the patch are CM-7's derived set."""

DECLINE = {
    "merge": "CK-8",
    "k": "K-8",
    "switch": {"module": "trestle.server.conductor", "name": "REAP_ON_SUCCESS", "declined": False},
    "labels": [
        "WR-OWN-3:success-no-survivor",
        "WR-CANCEL-2:success-no-bytes-after-finalization",
        "WR-PROOF-10:K-8",
    ],
    "clauses": ["A8.2"],
}
