"""Route one port protocol to the adapter of the realization it is asked about (L.RB-6.2; B3-C21).

The loop binds exactly one implementation per port protocol, but the reference tree has two
families behind `ResourceReads` and `ResourceCreate`: the Docker containers and the provisioning
record store (a `PROVISIONED` resource, whose submit is `ResourceCreate.create` and whose
authoritative read is `ResourceReads.observe`, B3-C21: provisioning is no port of its own).
`RealizationRouter` is the composition root's answer, and only routing: a call that carries a
spec goes by `spec.realization`; a call that carries only a target goes by the target's resource
kind (a provisioned record's `FoundRef` names it), or by the check id the provisioning port knows;
everything else is the container adapter's. It decides nothing about a resource and adds no member.
"""

from __future__ import annotations

from typing import Any

from trestle.workflow.declarations import RealizationKind

RECORD_KIND = "postgres_record"
RECORDED = "recorded"  # the provisioning port's convenience read


class RealizationRouter:
    """`ResourceReads` and `ResourceCreate` over a container adapter and a provisioning adapter."""

    def __init__(self, containers: Any, provisioning: Any) -> None:
        self._containers = containers
        self._provisioning = provisioning

    def _for(self, spec: Any) -> Any:
        provisioned = getattr(spec.realization, "value", spec.realization) == (
            RealizationKind.PROVISIONED.value
        )
        return self._provisioning if provisioned else self._containers

    def _for_target(self, target: Any) -> Any:
        kind = getattr(target, "resource_kind", "")
        return self._provisioning if kind == RECORD_KIND else self._containers

    # ---- ResourceReads

    def observe(self, spec: Any, lineage: Any, effect: Any) -> Any:
        return self._for(spec).observe(spec, lineage, effect)

    def check(self, check: Any, target: Any) -> Any:
        if check == RECORDED or getattr(target, "resource_kind", "") == RECORD_KIND:
            return self._provisioning.check(check, target)
        return self._containers.check(check, target)

    def endpoint(self, target: Any, vantage: Any) -> Any:
        return self._for_target(target).endpoint(target, vantage)

    # ---- ResourceCreate

    def launch_policy(self, spec: Any) -> Any:
        return self._for(spec).launch_policy(spec)

    def release_descriptor(self, call: Any) -> Any:
        spec = call.arguments.get("spec")
        if spec is not None:
            return self._for(spec).release_descriptor(call)
        return self._containers.release_descriptor(call)

    def create(self, spec: Any, ticket: Any) -> Any:
        return self._for(spec).create(spec, ticket)
