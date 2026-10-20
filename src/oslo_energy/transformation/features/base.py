"""Stateless feature-family contract; learned preprocessing belongs downstream."""

from abc import ABC, abstractmethod
from copy import deepcopy
import json
from typing import Any, Mapping

from oslo_energy.transformation.features.contracts import FeatureContext, FeatureResult
from oslo_energy.transformation.features.validation import validate_feature_result


class FeatureFamily(ABC):
    family_id: str
    version = "1"
    required_fields: tuple[str, ...] = ()
    uses_measurements = False

    def __init__(self, configuration: Mapping[str, Any] | None = None):
        self._configuration = self.validate_configuration(dict(configuration or {}))
        json.dumps(self._configuration, allow_nan=False)

    @property
    def configuration(self) -> dict[str, Any]:
        return deepcopy(self._configuration)

    def validate_configuration(self, configuration: dict[str, Any]) -> dict[str, Any]:
        if configuration:
            raise ValueError(f"{self.family_id} accepts no configuration settings")
        return {}

    @property
    @abstractmethod
    def columns(self) -> tuple[str, ...]:
        """Declare stable output order and names for this effective configuration."""

    @property
    def lookback_hours(self) -> int:
        return 0

    def build(self, context: FeatureContext) -> FeatureResult:
        isolated = FeatureContext(context.history.copy())
        result = self._build(isolated)
        validate_feature_result(context, result, self.columns, uses_measurements=self.uses_measurements)
        return result

    @abstractmethod
    def _build(self, context: FeatureContext) -> FeatureResult:
        """Use causal history or known calendar facts; never fit on future examples."""