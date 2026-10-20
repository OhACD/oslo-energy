"""Calendar facts known at forecast issue time, using the target's Oslo clock."""

import math

import pandas as pd

from oslo_energy.transformation.features.base import FeatureFamily
from oslo_energy.transformation.features.contracts import FeatureContext, FeatureResult


class Calendar(FeatureFamily):
    family_id = "calendar"

    @property
    def columns(self):
        return ("oslo_hour_sin", "oslo_hour_cos", "oslo_dayofweek_sin", "oslo_dayofweek_cos")

    def _build(self, context: FeatureContext) -> FeatureResult:
        targets = context.history.targets
        local = targets.tz_convert("Europe/Oslo")
        values = pd.DataFrame(index=targets)
        for name, numbers, period in (("oslo_hour", local.hour, 24), ("oslo_dayofweek", local.dayofweek, 7)):
            for suffix, function in (("sin", math.sin), ("cos", math.cos)):
                values[f"{name}_{suffix}"] = [function(2 * math.pi * number / period) for number in numbers]
        return FeatureResult(
            values.astype("float64"), pd.DataFrame(True, index=targets, columns=self.columns),
            pd.DataFrame("available", index=targets, columns=self.columns),
            pd.DataFrame({column: pd.Series(pd.NaT, index=targets, dtype="datetime64[ns, UTC]") for column in self.columns}),
        )