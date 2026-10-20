"""Ordinary least squares with training-only input scaling and JSON fitted state."""

import math

import pandas as pd

from oslo_energy.models.base import ModelAdapter
from oslo_energy.models.contracts import TrainingBatch


class LinearRegressionAdapter(ModelAdapter):
    model_id = "linear_regression"

    def validate_configuration(self, configuration: dict) -> dict:
        if set(configuration) - {"fit_intercept"} or type(configuration.get("fit_intercept", True)) is not bool:
            raise ValueError("Linear regression accepts only boolean fit_intercept")
        return {"fit_intercept": configuration.get("fit_intercept", True)}

    def required_columns(self, feature_columns: tuple[str, ...]) -> tuple[str, ...]:
        return feature_columns

    def _make_pipeline(self):
        try:
            from sklearn.linear_model import LinearRegression
            from sklearn.pipeline import Pipeline
            from sklearn.preprocessing import StandardScaler
        except ImportError as exc:
            raise ImportError("Install oslo-energy[modeling] for linear regression") from exc
        return Pipeline([
            ("scale", StandardScaler()),
            ("regression", LinearRegression(fit_intercept=self.configuration["fit_intercept"])),
        ])

    def _fit(self, batch: TrainingBatch) -> None:
        self._pipeline = self._make_pipeline()
        self._pipeline.fit(batch.features, batch.labels)

    def _predict(self, features: pd.DataFrame) -> pd.Series:
        return pd.Series(self._pipeline.predict(features), index=features.index, dtype="float64")

    def _state(self) -> dict:
        import sklearn

        scaler, regression = self._pipeline.named_steps["scale"], self._pipeline.named_steps["regression"]
        return {
            "sklearn_version": sklearn.__version__, "coefficients": regression.coef_.tolist(),
            "intercept": float(regression.intercept_), "mean": scaler.mean_.tolist(),
            "scale": scaler.scale_.tolist(), "variance": scaler.var_.tolist(),
            "sample_count": int(scaler.n_samples_seen_),
        }

    def _restore(self, state: dict) -> None:
        import sklearn

        required = {"sklearn_version", "coefficients", "intercept", "mean", "scale", "variance", "sample_count"}
        if set(state) != required or state["sklearn_version"] != sklearn.__version__:
            raise ValueError("Incompatible linear-regression state/dependency version")
        width = len(self.feature_columns)
        for field in ("coefficients", "mean", "scale", "variance"):
            values = state[field]
            if not isinstance(values, list) or len(values) != width or any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
                raise ValueError("Invalid fitted vector dimensions/values")
        if any(value <= 0 for value in state["scale"]) or any(value < 0 for value in state["variance"]):
            raise ValueError("Invalid fitted scaler state")
        if type(state["intercept"]) not in (int, float) or not math.isfinite(state["intercept"]):
            raise ValueError("Invalid fitted intercept")
        if type(state["sample_count"]) is not int or state["sample_count"] != self.fit_provenance["fitted_rows"] or state["sample_count"] < 1:
            raise ValueError("Fitted sample count contradicts provenance")
        self._pipeline = self._make_pipeline()
        scaler, regression = self._pipeline.named_steps["scale"], self._pipeline.named_steps["regression"]
        for name, values in (("mean_", state["mean"]), ("scale_", state["scale"]), ("var_", state["variance"])):
            setattr(scaler, name, pd.Series(values, dtype="float64").to_numpy())
        scaler.n_samples_seen_ = state["sample_count"]
        scaler.n_features_in_ = width
        scaler.feature_names_in_ = pd.Index(self.feature_columns).to_numpy()
        regression.coef_ = pd.Series(state["coefficients"], dtype="float64").to_numpy()
        regression.intercept_ = state["intercept"]
        regression.n_features_in_ = width