"""Explicit allowlist of implemented, tested cleaning policies."""

from types import MappingProxyType
from typing import Any, Mapping

from oslo_energy.transformation.cleaning.base import CleaningPolicy
from oslo_energy.transformation.cleaning.policies.preserve_and_flag import PreserveAndFlagPolicy


POLICY_REGISTRY: Mapping[str, type[CleaningPolicy]] = MappingProxyType({
    PreserveAndFlagPolicy.policy_id: PreserveAndFlagPolicy,
})


def get_policy(policy_id: str, configuration: Mapping[str, Any] | None = None) -> CleaningPolicy:
    """Resolve only registered policies, never arbitrary user-supplied imports."""
    if policy_id not in POLICY_REGISTRY:
        raise ValueError(f"Unknown cleaning policy: {policy_id}")
    return POLICY_REGISTRY[policy_id](configuration)