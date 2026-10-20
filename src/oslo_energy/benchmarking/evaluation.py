"""Coverage-aware regression metrics on fixed and explicitly paired timestamps."""

import math
from typing import Mapping, Sequence

import pandas as pd

from oslo_energy.models.contracts import PredictionResult, validate_numeric, validate_times


def _errors(reference: pd.Series, prediction: pd.Series, mask: pd.Series) -> dict:
    count = int(mask.sum())
    if not count:
        return {"count": 0, "mae": None, "rmse": None}
    residual = prediction.loc[mask] - reference.loc[mask]
    mae, rmse = float(residual.abs().mean()), math.sqrt(float(residual.pow(2).mean()))
    if not math.isfinite(mae) or not math.isfinite(rmse):
        raise ValueError("Non-finite benchmark errors")
    return {"count": count, "mae": mae, "rmse": rmse}


def evaluate_predictions(
    reference: pd.Series, predictions: Mapping[str, PredictionResult], *, baselines: Sequence[str],
) -> dict:
    """Do not shrink the intended grid or reward methods that abstain more often."""
    validate_times(reference.index)
    validate_numeric(reference)
    if not predictions or not baselines or len(set(baselines)) != len(baselines) or set(baselines) - set(predictions):
        raise ValueError("Provide predictions and distinct available baseline identities")
    for result in predictions.values():
        if not result.values.index.equals(reference.index):
            raise ValueError("Prediction timestamps must exactly match the intended reference grid")
    intended = len(reference)
    reference_available = reference.notna()
    available = {name: result.values.notna() for name, result in predictions.items()}
    individual = {}
    for name, result in predictions.items():
        scored = reference_available & available[name]
        individual[name] = {
            "intended": intended, "predicted": int(available[name].sum()),
            "reference_available": int(reference_available.sum()), "scored": int(scored.sum()),
            "unavailable_input": int(result.status.eq("unavailable_input").sum()),
            "failed": int(result.status.eq("failed").sum()),
            "prediction_coverage_of_intended": float(available[name].sum() / intended) if intended else None,
            "scored_coverage_of_intended": float(scored.sum() / intended) if intended else None,
            "errors": _errors(reference, result.values, scored),
        }
    paired = {}
    for name, result in predictions.items():
        paired[name] = {}
        for baseline in baselines:
            common = reference_available & available[name] & available[baseline]
            errors = _errors(reference, result.values, common)
            baseline_errors = _errors(reference, predictions[baseline].values, common)
            denominator = baseline_errors["mae"]
            skill = 1 - errors["mae"] / denominator if denominator not in (None, 0) else None
            paired[name][baseline] = {
                "model": errors, "baseline": baseline_errors, "mae_skill": skill,
                "skill_status": "available" if skill is not None else ("no_pairs" if not errors["count"] else "zero_baseline_mae"),
            }
    common = reference_available.copy()
    for mask in available.values():
        common &= mask
    return {
        "individual": individual, "paired_against_baselines": paired,
        "all_models_common": {name: _errors(reference, result.values, common) for name, result in predictions.items()},
        "reference_rule": "Score only observed finite labels; never fill labels or redefine intended timestamps",
    }