"""Known synthetic OLS, unfitted baselines, temporal guards, and JSON round trips."""

import pandas as pd
import pytest

from oslo_energy.ingestion.archive import Archive
from oslo_energy.models import ForecastBatch, TrainingBatch, get_model, load_model


@pytest.fixture
def batches():
    starts = pd.date_range("2025-01-01", periods=24, freq="h", tz="UTC")
    train = pd.DataFrame({"consumption_lag_1h": [float(number) for number in range(24)],
                          "consumption_lag_168h": [float((number % 5) ** 2) for number in range(24)]}, index=starts)
    labels = 2 * train.iloc[:, 0] + 3 * train.iloc[:, 1] + 5
    cutoff = starts[-1] + pd.Timedelta(hours=1)
    batch = TrainingBatch(
        train, labels, pd.Series("train", index=starts), pd.Series(starts + pd.Timedelta(hours=1), index=starts),
        starts[0], cutoff, cutoff, "a" * 64, 24,
    )
    future = pd.date_range(cutoff, periods=3, freq="h")
    features = pd.DataFrame({"consumption_lag_1h": [25.0, 26.0, 27.0], "consumption_lag_168h": [1.0, 4.0, 9.0]}, index=future)
    return batch, ForecastBatch(features, pd.Series(future, index=future))


@pytest.mark.parametrize("model_id, column", [("seasonal_naive", "consumption_lag_168h"), ("persistence", "consumption_lag_1h")])
def test_baseline_requires_only_its_own_lag_and_has_no_fallback(batches, model_id, column):
    _, forecast = batches
    features = forecast.features.copy()
    other = next(name for name in features if name != column)
    features.loc[features.index[0], other] = float("nan")
    features.loc[features.index[1], column] = float("nan")
    result = get_model(model_id).predict(ForecastBatch(features, forecast.issue_times))
    assert result.values.iloc[0] == features[column].iloc[0]
    assert pd.isna(result.values.iloc[1])
    assert result.status.iloc[1] == "unavailable_input"
    assert len(result.values) == 3


def test_linear_recovers_known_relation_and_scaler_is_train_only(batches):
    train, forecast = batches
    model = get_model("linear_regression").fit(train)
    actual = model.predict(forecast)
    expected = 2 * forecast.features.iloc[:, 0] + 3 * forecast.features.iloc[:, 1] + 5
    assert actual.values.tolist() == pytest.approx(expected.tolist(), abs=1e-10)
    assert model._state()["mean"] == pytest.approx(train.features.mean().tolist())
    assert model.fit_provenance["fitted_rows"] == 24
    assert model.fit_provenance["latest_label_available_at"] == model.fit_provenance["fit_cutoff_utc"]


@pytest.mark.parametrize("model_id", ["seasonal_naive", "persistence", "linear_regression"])
def test_json_checkpoint_roundtrip_preserves_predictions_and_refuses_overwrite(tmp_path, batches, model_id):
    train, forecast = batches
    model = get_model(model_id)
    if model.requires_fit:
        model.fit(train)
    path = tmp_path / "state.json"
    model.save(path)
    restored = load_model(path)
    pd.testing.assert_series_equal(model.predict(forecast).values, restored.predict(forecast).values, check_exact=False, atol=1e-12, rtol=1e-12)
    assert restored.fit_provenance == model.fit_provenance
    with pytest.raises(FileExistsError):
        model.save(path)


def test_linear_abstains_on_missing_inputs_without_changing_grid(batches):
    train, forecast = batches
    features = forecast.features.copy()
    features.iloc[0, 0] = float("nan")
    result = get_model("linear_regression").fit(train).predict(ForecastBatch(features, forecast.issue_times))
    assert result.values.index.equals(features.index)
    assert result.values.isna().sum() == 1
    assert result.status.iloc[0] == "unavailable_input"


def test_heldout_data_cannot_change_fitted_state(batches):
    train, forecast = batches
    model = get_model("linear_regression").fit(train)
    before = model._state()
    changed = ForecastBatch(forecast.features * 100000, forecast.issue_times)
    model.predict(changed)
    assert model._state() == before
    assert get_model("linear_regression").fit(train)._state() == before


def test_fit_and_predict_temporal_schema_guards(batches):
    train, forecast = batches
    model = get_model("linear_regression")
    with pytest.raises(ValueError, match="fitted"):
        model.predict(forecast)
    with pytest.raises(ValueError, match="TrainingBatch"):
        model.fit(forecast)
    model.fit(train)
    with pytest.raises(ValueError, match="schema"):
        model.predict(ForecastBatch(forecast.features.iloc[:, ::-1], forecast.issue_times))
    with pytest.raises(ValueError, match="fit cutoff"):
        model.predict(ForecastBatch(train.features, pd.Series(train.features.index, index=train.features.index)))
    train.splits.iloc[0] = "validation"
    with pytest.raises(ValueError, match="training split"):
        model.fit(train)


def test_corrupt_checkpoint_vectors_and_versions_are_rejected(tmp_path, batches):
    train, _ = batches
    path = tmp_path / "state.json"
    get_model("linear_regression").fit(train).save(path)
    payload = Archive(path).read()
    payload["state"]["coefficients"] = [1.0]
    path.unlink()
    Archive(path).write(payload)
    with pytest.raises(ValueError, match="vector"):
        load_model(path)


def test_unknown_models_and_settings_fail():
    for args in (("arbitrary.module",), ("linear_regression", "2"), ("persistence", "1", {"fallback": True}),
                 ("linear_regression", "1", {"fit_intercept": 1})):
        with pytest.raises(ValueError):
            get_model(*args)