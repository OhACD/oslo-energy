"""Unfitted reference forecasts with no implicit imputation or fallback."""

import pandas as pd

from oslo_energy.models.base import ModelAdapter


class SeasonalNaive(ModelAdapter):
    model_id = "seasonal_naive"
    requires_fit = False
    lag_column = "consumption_lag_168h"

    def required_columns(self, feature_columns: tuple[str, ...]) -> tuple[str, ...]:
        return (self.lag_column,)

    def _predict(self, features: pd.DataFrame) -> pd.Series:
        return features[self.lag_column].astype("float64").copy()


class Persistence(SeasonalNaive):
    model_id = "persistence"
    lag_column = "consumption_lag_1h"