"""Resolve only known, versioned feature families."""

from types import MappingProxyType

from oslo_energy.transformation.features.families.calendar import Calendar
from oslo_energy.transformation.features.families.consumption_lags import ConsumptionLags


FEATURE_REGISTRY = MappingProxyType({
    ConsumptionLags.family_id: ConsumptionLags, Calendar.family_id: Calendar,
})


def get_family(family_id: str, version: str = "1", configuration: dict | None = None):
    if family_id not in FEATURE_REGISTRY:
        raise ValueError(f"Unknown feature family: {family_id}")
    family = FEATURE_REGISTRY[family_id](configuration)
    if version != family.version:
        raise ValueError(f"Unsupported version for {family_id}: {version}")
    return family