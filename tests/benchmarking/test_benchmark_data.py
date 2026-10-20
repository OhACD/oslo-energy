"""Fit cutoff and train-only batch gates independent of estimator internals."""

from dataclasses import replace

import pandas as pd
import pytest

from oslo_energy.benchmarking.data import BenchmarkDataset
from oslo_energy.transformation.research_audit import ResearchWindow


def dataset():
    windows = tuple(ResearchWindow(name, pd.Timestamp(day).date(), pd.Timestamp(day).date()) for name, day in (
        ("train", "2025-01-01"), ("validation", "2025-01-02"), ("test", "2025-01-03"),
    ))
    index = pd.date_range(windows[0].start_utc, windows[-1].end_utc, inclusive="left", freq="h")
    features = pd.DataFrame({"consumption_lag_1h": range(len(index)), "consumption_lag_168h": range(len(index))}, index=index, dtype="float64")
    metadata = pd.DataFrame({"split": [name for name in ("train", "validation", "test") for _ in range(24)],
                             "target_end_utc": index + pd.Timedelta(hours=1), "forecast_issue_time_utc": index}, index=index)
    labels = pd.Series([float(number) for number in range(48)], index=index[:48])
    return BenchmarkDataset(features, labels, metadata, windows, "validation", 0, "a" * 64, {})


def test_training_batch_is_complete_and_delayed_labels_respect_cutoff():
    data = dataset()
    regular = data.training_batch(tuple(data.features.columns))
    assert len(regular.features) == 24
    assert regular.label_available_at.max() == regular.fit_cutoff
    delayed = replace(data, publication_delay_hours=1).training_batch(tuple(data.features.columns))
    assert len(delayed.features) == 23
    assert delayed.fit_cutoff == regular.fit_cutoff
    assert len(data.forecast_batch().features) == 24
    assert len(data.reference()) == 24


@pytest.mark.parametrize("broken", ["split", "outside", "late_label", "alignment", "missing", "empty"])
def test_training_contract_rejects_invalid_or_leaky_batches(broken):
    batch = dataset().training_batch(("consumption_lag_1h",))
    updates = {}
    if broken == "split":
        updates["splits"] = pd.Series("validation", index=batch.features.index)
    elif broken == "outside":
        updates["train_end"] = batch.features.index[-1]
    elif broken == "late_label":
        updates["label_available_at"] = batch.label_available_at + pd.Timedelta(days=1)
    elif broken == "alignment":
        updates["labels"] = batch.labels.shift(1, freq="h")
    elif broken == "missing":
        features = batch.features.copy()
        features.iloc[0, 0] = float("nan")
        updates["features"] = features
    else:
        updates = {name: getattr(batch, name).iloc[:0] for name in ("features", "labels", "splits", "label_available_at")}
    with pytest.raises(ValueError):
        replace(batch, **updates)


def test_heldout_labels_are_not_supplied_to_forecast_or_fit():
    data = dataset()
    original = data.training_batch(tuple(data.features.columns))
    changed = data.labels.copy()
    changed.loc[data.metadata["split"].reindex(changed.index).eq("validation")] = 999999
    altered = replace(data, labels=changed).training_batch(tuple(data.features.columns))
    pd.testing.assert_series_equal(original.labels, altered.labels)
    assert not hasattr(data.forecast_batch(), "labels")