"""Explicit model resolution and non-executable JSON checkpoint loading."""

from pathlib import Path
from types import MappingProxyType

import pandas as pd

from oslo_energy.ingestion.archive import Archive
from oslo_energy.models.adapters.linear_regression import LinearRegressionAdapter
from oslo_energy.models.adapters.naive import Persistence, SeasonalNaive
from oslo_energy.models.contracts import validate_features


MODEL_REGISTRY = MappingProxyType({
    SeasonalNaive.model_id: SeasonalNaive, Persistence.model_id: Persistence,
    LinearRegressionAdapter.model_id: LinearRegressionAdapter,
})


def get_model(model_id: str, version: str = "1", configuration: dict | None = None):
    if model_id not in MODEL_REGISTRY:
        raise ValueError(f"Unknown model: {model_id}")
    model = MODEL_REGISTRY[model_id](configuration)
    if model.version != version:
        raise ValueError("Unsupported model version")
    return model


def load_model(path: Path):
    payload = Archive(path).read()
    required = {"schema_version", "model_id", "model_version", "configuration", "feature_columns", "fit_provenance", "state"}
    if not isinstance(payload, dict) or set(payload) != required or payload["schema_version"] != "1":
        raise ValueError("Incompatible model checkpoint")
    model = get_model(payload["model_id"], payload["model_version"], payload["configuration"])
    columns = payload["feature_columns"]
    if not isinstance(columns, list) or any(not isinstance(column, str) for column in columns) or len(set(columns)) != len(columns):
        raise ValueError("Invalid fitted feature schema")
    model.feature_columns = tuple(columns)
    model.fit_provenance = payload["fit_provenance"]
    if model.requires_fit:
        validate_features(pd.DataFrame(columns=columns, index=pd.DatetimeIndex([], tz="UTC"), dtype="float64"))
        provenance = model.fit_provenance
        if not isinstance(provenance, dict) or provenance.get("kind") != "training_only":
            raise ValueError("Missing training-only model provenance")
        try:
            times = {name: pd.Timestamp(provenance[name]) for name in (
                "train_start_utc", "train_end_utc_exclusive", "fit_cutoff_utc", "first_fitted_target_utc",
                "last_fitted_target_utc", "latest_label_available_at",
            )}
            if any(pd.isna(value) or value.tzinfo is None for value in times.values()):
                raise ValueError("Invalid checkpoint timestamps")
            if not (times["train_start_utc"] <= times["first_fitted_target_utc"] <= times["last_fitted_target_utc"] < times["train_end_utc_exclusive"] <= times["fit_cutoff_utc"]):
                raise ValueError("Invalid checkpoint training interval")
            if times["latest_label_available_at"] > times["fit_cutoff_utc"]:
                raise ValueError("Checkpoint label availability exceeds fit cutoff")
        except (KeyError, TypeError) as exc:
            raise ValueError("Incomplete training provenance") from exc
    elif model.fit_provenance != {"kind": "not_required"} or columns:
        raise ValueError("Unfitted baseline checkpoint must not claim learned state")
    model._restore(payload["state"])
    return model