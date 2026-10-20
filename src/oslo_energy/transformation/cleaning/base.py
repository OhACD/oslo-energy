"""Policy execution boundary with shared input isolation and result validation."""

from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import replace
import json
from typing import Any, Mapping

from oslo_energy.transformation.cleaning.contracts import CleaningContext, CleaningResult, PolicyCapabilities
from oslo_energy.transformation.cleaning.validation import validate_policy_result


class CleaningPolicy(ABC):
    """Enforce shared invariants while leaving decisions to concrete policies."""

    policy_id: str
    version: str
    capabilities = PolicyCapabilities()

    def __init__(self, configuration: Mapping[str, Any] | None = None):
        self._configuration = self.validate_configuration(dict(configuration or {}))
        json.dumps(self._configuration, allow_nan=False)

    @property
    def configuration(self) -> dict[str, Any]:
        return deepcopy(self._configuration)

    def validate_configuration(self, configuration: dict[str, Any]) -> dict[str, Any]:
        """Policies without settings reject configuration rather than ignoring it."""
        if configuration:
            raise ValueError(f"{self.policy_id} accepts no configuration settings")
        return {}

    def apply(self, context: CleaningContext) -> CleaningResult:
        """Isolate inputs and validate the policy's declared permissions."""
        isolated = replace(
            context, observations=context.observations.copy(deep=True),
            normalization_report=deepcopy(dict(context.normalization_report)),
            provenance=deepcopy(dict(context.provenance)),
        )
        result = self._apply(isolated)
        validate_policy_result(context, result, self.capabilities)
        return result

    @abstractmethod
    def _apply(self, context: CleaningContext) -> CleaningResult:
        """Return decisions without implementing input loading or reporting."""