"""Demo credential grant adapters for the workflow loop's Demo Credential Serving family
(L.RB-9.1, L.RB-9.2; B3-C8, B3-C9, B3-C11, D-9: AWS is DEMO ONLY, never real).

`demo` holds `DemoGrant` (`GrantReads` + `GrantRefresh`) over the stub issuer on loopback,
`delivery` the credential channel (`GrantDelivery`: a mounted refreshable file) and
`consumer_probe` the authenticated call made from inside a consumer (a container by `docker exec`,
a local app). The
adapters import only the standard library and `trestle.workflow` (BFD-47, C.5 step 4); the legacy
subpackages are untouched. `fakes/grant.py` is the stdlib-only fake the same suite runs against.
"""

from trestle_packs.grant.consumer_probe import ArgvRunner, ContainerExecProbe, LocalAppProbe
from trestle_packs.grant.delivery import (
    CHANNEL_FILE,
    CHANNEL_MOUNT,
    ChannelDelivery,
    channel_directory,
    channel_mount,
    provision_channel,
    write_channel,
)
from trestle_packs.grant.demo import (
    CREDENTIAL_INTERACTIVE,
    CREDENTIAL_STALE,
    DEMO_CREDENTIAL_KIND,
    GRANT_ISSUER_UNREACHABLE,
    ConsumerProbe,
    DemoGrant,
    IssuerClient,
    ProbeReading,
)

__all__ = [
    "CHANNEL_FILE",
    "CHANNEL_MOUNT",
    "CREDENTIAL_INTERACTIVE",
    "CREDENTIAL_STALE",
    "DEMO_CREDENTIAL_KIND",
    "GRANT_ISSUER_UNREACHABLE",
    "ArgvRunner",
    "ChannelDelivery",
    "ConsumerProbe",
    "ContainerExecProbe",
    "DemoGrant",
    "IssuerClient",
    "LocalAppProbe",
    "ProbeReading",
    "channel_directory",
    "channel_mount",
    "provision_channel",
    "write_channel",
]
