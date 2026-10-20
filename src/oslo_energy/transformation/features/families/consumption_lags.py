"""Exact elapsed-hour consumption lookups through the causal history boundary."""

import pandas as pd

from oslo_energy.transformation.features.base import FeatureFamily
from oslo_energy.transformation.features.contracts import FeatureContext, FeatureResult


class ConsumptionLags(FeatureFamily):
    family_id = "consumption_lags"
    required_fields = ("period_start_utc", "period_end_utc", "consumption")
    uses_measurements = True

    def validate_configuration(self, configuration):
        if set(configuration) - {"lags"}:
            raise ValueError("Unknown consumption_lags configuration")
        lags = configuration.get("lags", [1, 24, 168])
        if (
            not isinstance(lags, list) or not lags
            or any(type(hours) is not int or hours <= 0 for hours in lags)
            or len(set(lags)) != len(lags)
        ):
            raise ValueError("lags must be distinct positive integer hours")
        return {"lags": sorted(lags)}

    @property
    def columns(self):
        return tuple(f"consumption_lag_{hours}h" for hours in self.configuration["lags"])

    @property
    def lookback_hours(self):
        return max(self.configuration["lags"])

    def _build(self, context: FeatureContext) -> FeatureResult:
        parts = [context.history.lag(hours) for hours in self.configuration["lags"]]
        return FeatureResult(*(pd.concat([getattr(part, field) for part in parts], axis=1)
                               for field in ("values", "valid", "reasons", "available_at")))