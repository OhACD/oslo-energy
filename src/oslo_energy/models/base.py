"""Guarded estimator execution; labels never enter the prediction interface."""

from abc import ABC, abstractmethod
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pandas as pd

from oslo_energy.ingestion.archive import Archive
from oslo_energy.models.contracts import ForecastBatch, PredictionResult, TrainingBatch


class ModelAdapter(ABC):
    model_id: str
    version = "1"
    requires_fit = True

    def __init__(self, configuration: dict | None = None):
        self._configuration = self.validate_configuration(dict(configuration or {}))
        json.dumps(self._configuration, allow_nan=False)
        self.feature_columns: tuple[str, ...] = ()
        self.fit_provenance: dict = {"kind": "not_required"} if not self.requires_fit else {}

    @property
    def configuration(self) -> dict:
        return deepcopy(self._configuration)

    def validate_configuration(self, configuration: dict) -> dict:
        if configuration:
            raise ValueError(f"{self.model_id} accepts no configuration settings")
        return {}

    @abstractmethod
    def required_columns(self, feature_columns: tuple[str, ...]) -> tuple[str, ...]:
        """Identify only the inputs actually required by this method."""

    def fit(self, batch: TrainingBatch) -> "ModelAdapter":
        if not isinstance(batch, TrainingBatch):
            raise ValueError("fit requires an explicitly validated TrainingBatch")
        batch.validate()
        if not self.requires_fit:
            return self
        isolated = TrainingBatch(
            batch.features.copy(deep=True), batch.labels.copy(deep=True), batch.splits.copy(deep=True),
            batch.label_available_at.copy(deep=True), batch.train_start, batch.train_end, batch.fit_cutoff,
            batch.feature_manifest_sha256, batch.intended_train_count,
        )
        self.feature_columns = tuple(isolated.features.columns)
        self.fit_provenance = {}
        self._fit(isolated)
        self.fit_provenance = {
            "kind": "training_only", "train_start_utc": batch.train_start.isoformat(),
            "train_end_utc_exclusive": batch.train_end.isoformat(), "fit_cutoff_utc": batch.fit_cutoff.isoformat(),
            "first_fitted_target_utc": batch.features.index.min().isoformat(),
            "last_fitted_target_utc": batch.features.index.max().isoformat(),
            "latest_label_available_at": batch.label_available_at.max().isoformat(),
            "intended_train_count": batch.intended_train_count, "fitted_rows": len(batch.features),
            "excluded_train_rows": batch.intended_train_count - len(batch.features),
            "training_index_sha256": hashlib.sha256("\n".join(batch.features.index.map(lambda value: value.isoformat())).encode()).hexdigest(),
            "feature_manifest_sha256": batch.feature_manifest_sha256,
        }
        return self

    def predict(self, batch: ForecastBatch) -> PredictionResult:
        if not isinstance(batch, ForecastBatch):
            raise ValueError("predict requires a label-free ForecastBatch")
        isolated = ForecastBatch(batch.features.copy(deep=True), batch.issue_times.copy(deep=True))
        if self.requires_fit:
            if not self.fit_provenance:
                raise ValueError("Model must be fitted before prediction")
            if tuple(isolated.features.columns) != self.feature_columns:
                raise ValueError("Prediction feature schema/order differs from fitted schema")
            if not isolated.issue_times.ge(pd.Timestamp(self.fit_provenance["fit_cutoff_utc"])).all():
                raise ValueError("Prediction precedes the model fit cutoff")
        required = self.required_columns(tuple(isolated.features.columns))
        if set(required) - set(isolated.features.columns):
            raise ValueError("Required model inputs are absent from the feature recipe")
        eligible = isolated.features.loc[:, list(required)].notna().all(axis=1)
        values = pd.Series(float("nan"), index=isolated.features.index, name="prediction", dtype="float64")
        status = pd.Series("unavailable_input", index=values.index, name="status")
        if eligible.any():
            predicted = self._predict(isolated.features.loc[eligible, list(required)])
            if not isinstance(predicted, pd.Series) or not predicted.index.equals(values.index[eligible]):
                raise ValueError("Estimator predictions must preserve the requested timestamps")
            values.loc[eligible] = predicted
            status.loc[eligible] = "predicted"
        return PredictionResult(values, status)

    def _fit(self, batch: TrainingBatch) -> None:
        raise NotImplementedError

    @abstractmethod
    def _predict(self, features: pd.DataFrame) -> pd.Series:
        """Predict without access to reference labels or retrospective masks."""

    def _state(self) -> dict:
        return {}

    def _restore(self, state: dict) -> None:
        if state:
            raise ValueError("Stateless models cannot restore fitted parameters")

    def save(self, path: Path) -> None:
        if self.requires_fit and not self.fit_provenance:
            raise ValueError("Cannot save an unfitted learned model")
        Archive(path).write({
            "schema_version": "1", "model_id": self.model_id, "model_version": self.version,
            "configuration": self.configuration, "feature_columns": list(self.feature_columns),
            "fit_provenance": deepcopy(self.fit_provenance), "state": self._state(),
        })