"""Known-answer scoring, coverage denominators, and fixed-reference alignment."""

import math

import pandas as pd
import pytest

from oslo_energy.benchmarking.evaluation import evaluate_predictions
from oslo_energy.models.contracts import PredictionResult


def series(values):
    return pd.Series(values, index=pd.date_range("2025-01-01", periods=len(values), freq="h", tz="UTC"), dtype="float64")


def predictions(values, *, failed=False):
    values = series(values)
    status = pd.Series("predicted", index=values.index)
    status.loc[values.isna()] = "failed" if failed else "unavailable_input"
    return PredictionResult(values, status)


def test_known_metrics_use_same_pairs_and_named_coverage_denominators():
    report = evaluate_predictions(series([1, 2, None, 4]), {
        "naive": predictions([2, 4, 8, None]), "linear": predictions([1, None, 9, 7]),
    }, baselines=["naive"])
    naive = report["individual"]["naive"]
    assert naive["predicted"] == 3
    assert naive["reference_available"] == 3
    assert naive["scored"] == 2
    assert naive["errors"]["mae"] == 1.5
    assert naive["errors"]["rmse"] == pytest.approx(math.sqrt(2.5))
    assert naive["prediction_coverage_of_intended"] == 0.75
    assert naive["scored_coverage_of_intended"] == 0.5
    paired = report["paired_against_baselines"]["linear"]["naive"]
    assert paired["model"]["count"] == 1
    assert paired["mae_skill"] == 1
    assert report["all_models_common"]["naive"]["count"] == 1


def test_perfect_baseline_has_explicit_undefined_relative_skill():
    report = evaluate_predictions(series([1, 2]), {"naive": predictions([1, 2])}, baselines=["naive"])
    assert report["individual"]["naive"]["errors"]["mae"] == 0
    assert report["paired_against_baselines"]["naive"]["naive"]["skill_status"] == "zero_baseline_mae"


def test_no_reference_or_no_predictions_does_not_claim_perfect_error():
    report = evaluate_predictions(series([None, 2]), {
        "naive": predictions([1, None]), "failed": predictions([None, None], failed=True),
    }, baselines=["naive"])
    assert report["individual"]["naive"]["errors"] == {"count": 0, "mae": None, "rmse": None}
    assert report["individual"]["failed"]["failed"] == 2
    assert report["individual"]["failed"]["unavailable_input"] == 0
    assert report["paired_against_baselines"]["failed"]["naive"]["skill_status"] == "no_pairs"


def test_misaligned_prediction_timestamps_are_rejected_not_intersected():
    result = predictions([1, 2])
    shifted = PredictionResult(result.values.shift(1, freq="h"), result.status.shift(1, freq="h"))
    with pytest.raises(ValueError, match="exactly match"):
        evaluate_predictions(series([1, 2]), {"naive": shifted}, baselines=["naive"])


@pytest.mark.parametrize("broken", ["infinite", "duplicate", "status", "missing_status"])
def test_prediction_contract_rejects_invalid_results(broken):
    result = predictions([1, None])
    values, status = result.values.copy(), result.status.copy()
    if broken == "infinite":
        values.iloc[0] = float("inf")
    elif broken == "duplicate":
        values.index = pd.DatetimeIndex([values.index[0]] * 2)
        status.index = values.index
    elif broken == "status":
        status.iloc[1] = "predicted"
    else:
        status = status.iloc[:1]
    with pytest.raises(ValueError):
        PredictionResult(values, status)