"""Timestamp and prediction contracts shared by adapters and benchmarking."""

from dataclasses import dataclass
import math
import re

import pandas as pd


def validate_times(index: pd.Index) -> None:
    if (
        not isinstance(index, pd.DatetimeIndex) or index.tz is None or index.hasnans
        or index.duplicated().any() or not index.is_monotonic_increasing
        or not index.equals(index.floor("h"))
    ):
        raise ValueError("Expected unique ordered timezone-aware hourly timestamps")


def validate_numeric(values: pd.Series | pd.DataFrame, *, missing: bool = True) -> None:
    columns = [values] if isinstance(values, pd.Series) else [values[column] for column in values]
    for column in columns:
        if not pd.api.types.is_numeric_dtype(column) or pd.api.types.is_bool_dtype(column):
            raise ValueError("Expected numeric measurements, not booleans or strings")
        if not column.dropna().map(math.isfinite).all() or (not missing and column.isna().any()):
            raise ValueError("Measurements must be finite with explicit missing-value handling")


def validate_features(features: pd.DataFrame, *, missing: bool = True) -> None:
    validate_times(features.index)
    if features.columns.duplicated().any() or not len(features.columns):
        raise ValueError("Feature columns must be nonempty and unique")
    forbidden = {"target_consumption", "target_available", "split", "complete_example", "training_example_eligible"}
    if forbidden.intersection(features.columns):
        raise ValueError("Labels and retrospective metadata cannot enter model features")
    validate_numeric(features, missing=missing)


@dataclass(frozen=True)
class ForecastBatch:
    """Predictors and issue times only; reference labels are deliberately absent."""

    features: pd.DataFrame
    issue_times: pd.Series

    def __post_init__(self):
        validate_features(self.features)
        if not self.issue_times.index.equals(self.features.index):
            raise ValueError("Issue times must align exactly with forecast features")
        if not isinstance(self.issue_times.dtype, pd.DatetimeTZDtype) or not self.issue_times.eq(self.features.index).all():
            raise ValueError("One-hour forecasts must be issued at target start")


@dataclass(frozen=True)
class TrainingBatch:
    """Eligible train rows with checked label availability and fitted-input lineage."""

    features: pd.DataFrame
    labels: pd.Series
    splits: pd.Series
    label_available_at: pd.Series
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    fit_cutoff: pd.Timestamp
    feature_manifest_sha256: str
    intended_train_count: int

    def __post_init__(self):
        self.validate()

    def validate(self) -> None:
        validate_features(self.features, missing=False)
        validate_numeric(self.labels, missing=False)
        if self.features.empty:
            raise ValueError("No eligible training examples")
        if type(self.intended_train_count) is not int or self.intended_train_count < len(self.features):
            raise ValueError("Invalid intended training-example count")
        if not isinstance(self.feature_manifest_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", self.feature_manifest_sha256):
            raise ValueError("Training requires a pinned feature manifest identity")
        for series in (self.labels, self.splits, self.label_available_at):
            if not series.index.equals(self.features.index):
                raise ValueError("Training fields must have identical timestamps")
        if not self.splits.eq("train").all():
            raise ValueError("Only the training split may be fitted")
        for timestamp in (self.train_start, self.train_end, self.fit_cutoff):
            if timestamp.tzinfo is None or pd.isna(timestamp):
                raise ValueError("Training boundaries must be timezone aware")
        if self.train_start >= self.train_end or self.train_end > self.fit_cutoff:
            raise ValueError("Invalid chronological training boundaries")
        if not ((self.features.index >= self.train_start) & (self.features.index < self.train_end)).all():
            raise ValueError("Training examples fall outside the training window")
        if (
            not isinstance(self.label_available_at.dtype, pd.DatetimeTZDtype)
            or self.label_available_at.isna().any()
            or not self.label_available_at.le(self.fit_cutoff).all()
            or not self.label_available_at.ge(self.features.index + pd.Timedelta(hours=1)).all()
        ):
            raise ValueError("Training labels were not available by the fit cutoff")


@dataclass(frozen=True)
class PredictionResult:
    """A value or explicit abstention/failure for every intended timestamp."""

    values: pd.Series
    status: pd.Series

    def __post_init__(self):
        validate_times(self.values.index)
        validate_numeric(self.values)
        if not self.status.index.equals(self.values.index):
            raise ValueError("Prediction status and values must have identical timestamps")
        if not self.status.isin(["predicted", "unavailable_input", "failed"]).all():
            raise ValueError("Unknown prediction status")
        if not self.status.eq("predicted").eq(self.values.notna()).all():
            raise ValueError("Prediction status must agree with finite-value availability")