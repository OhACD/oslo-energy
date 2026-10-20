"""Enforce feature schema, missingness, and declared availability boundaries."""

import math

import pandas as pd

from oslo_energy.transformation.features.contracts import FeatureContext, FeatureResult


def validate_feature_result(
    context: FeatureContext, result: FeatureResult, columns: tuple[str, ...], *, uses_measurements: bool,
) -> None:
    for frame in (result.values, result.valid, result.reasons, result.available_at):
        if list(frame.columns) != list(columns) or not frame.index.equals(context.history.targets):
            raise ValueError("Feature result violates declared schema or timestamp alignment")
    for column in columns:
        values, valid = result.values[column], result.valid[column]
        if valid.dtype != bool or not valid.eq(values.notna()).all():
            raise ValueError("Feature validity must exactly describe available numeric values")
        if not pd.api.types.is_float_dtype(values) or not values.dropna().map(math.isfinite).all():
            raise ValueError("Feature values must be finite floats or explicit missing values")
        if not result.reasons[column].eq("available").eq(valid).all():
            raise ValueError("Feature reasons must agree with validity")
        available = result.available_at[column]
        if uses_measurements and available.loc[valid].isna().any():
            raise ValueError("Measurement features require availability provenance")
        issues = pd.Series(context.history.issue_times, index=values.index)
        if (valid & available.gt(issues)).any():
            raise ValueError("Feature contains information unavailable at forecast issue time")