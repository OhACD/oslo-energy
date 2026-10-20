"""Prove exact lag timestamps and no target/future-value influence on earlier features."""

from dataclasses import replace

import pandas as pd
import pytest

from oslo_energy.transformation.features import CausalHistory, FeatureContext, get_family
from oslo_energy.transformation.features.families.consumption_lags import ConsumptionLags


def history(start="2025-01-01", count=240):
    starts = pd.date_range(start, periods=count, freq="h", tz="UTC")
    return pd.DataFrame({"period_start_utc": starts, "period_end_utc": starts + pd.Timedelta(hours=1),
                         "consumption": [float(index + 1) for index in range(count)]})


def context(frame, targets, delay=0, issues=None):
    return FeatureContext(CausalHistory(frame, targets, issues if issues is not None else targets, delay))


def test_exact_lags_use_pre_window_history_without_compressing_missing_rows():
    frame = history().drop(index=198)
    targets = pd.date_range("2025-01-09T07:00:00Z", periods=2, freq="h")
    result = get_family("consumption_lags").build(context(frame, targets))
    assert pd.isna(result.values.iloc[0]["consumption_lag_1h"])
    assert result.reasons.iloc[0]["consumption_lag_1h"] == "absent_source_hour"
    assert result.values.iloc[1]["consumption_lag_1h"] == 200
    assert result.values.iloc[0]["consumption_lag_168h"] == 32


@pytest.mark.parametrize("family", ["consumption_lags", "calendar"])
@pytest.mark.parametrize("cutoff_position", [168, 180, 200, 220])
def test_target_and_future_perturbations_do_not_change_earlier_features(family, cutoff_position):
    frame = history()
    cutoff = frame.loc[cutoff_position, "period_start_utc"]
    targets = pd.date_range(frame.loc[cutoff_position - 10, "period_start_utc"], cutoff, freq="h")
    original = get_family(family).build(context(frame, targets))
    altered = frame.copy()
    altered.loc[altered["period_start_utc"] >= cutoff, "consumption"] = 999999.0
    actual = get_family(family).build(context(altered, targets))
    pd.testing.assert_frame_equal(original.values, actual.values)
    pd.testing.assert_frame_equal(original.valid, actual.valid)
    truncated = frame.loc[frame["period_end_utc"] <= cutoff]
    pd.testing.assert_frame_equal(original.values, get_family(family).build(context(truncated, targets)).values)


def test_publication_delay_masks_completed_but_unavailable_input():
    frame = history()
    targets = pd.DatetimeIndex([frame.loc[200, "period_start_utc"]])
    result = get_family("consumption_lags").build(context(frame, targets, delay=1))
    assert result.reasons.iloc[0]["consumption_lag_1h"] == "not_available_at_issue"
    assert pd.isna(result.values.iloc[0]["consumption_lag_1h"])
    assert result.valid.iloc[0]["consumption_lag_24h"]


def test_nulls_prehistory_and_unrelated_missing_hours_are_distinct():
    frame = history(count=30).drop(index=4)
    frame.loc[23, "consumption"] = float("nan")
    targets = pd.DatetimeIndex([frame.loc[24, "period_start_utc"]])
    result = get_family("consumption_lags").build(context(frame, targets))
    assert result.reasons.iloc[0]["consumption_lag_1h"] == "missing_measurement"
    assert result.reasons.iloc[0]["consumption_lag_168h"] == "insufficient_history"
    assert result.valid.iloc[0]["consumption_lag_24h"]


@pytest.mark.parametrize("day", ["2025-03-30", "2025-10-26"])
def test_dst_lags_are_elapsed_utc_and_calendar_is_local(day):
    frame = history(start=f"{day}T00:00:00", count=8)
    targets = pd.DatetimeIndex(frame["period_start_utc"].iloc[1:])
    result = get_family("consumption_lags", configuration={"lags": [1]}).build(context(frame, targets))
    assert result.values.iloc[:, 0].tolist() == [float(value) for value in range(1, 8)]
    calendar = get_family("calendar").build(context(frame, targets))
    assert calendar.valid.all().all()
    assert (calendar.values["oslo_hour_sin"] ** 2 + calendar.values["oslo_hour_cos"] ** 2).round(12).eq(1).all()
    if day == "2025-10-26":
        first_two = pd.DatetimeIndex(frame["period_start_utc"].iloc[:2])
        repeated = get_family("calendar").build(context(frame, first_two)).values
        assert repeated.index[0] != repeated.index[1]
        assert repeated.iloc[0].equals(repeated.iloc[1])


@pytest.mark.parametrize("lags", [[0], [-1], [True], [1, 1], [], [1.5]])
def test_invalid_lags_fail(lags):
    with pytest.raises(ValueError):
        get_family("consumption_lags", configuration={"lags": lags})


def test_unregistered_families_versions_and_unknown_settings_fail():
    for args in (("some.module",), ("calendar", "2"), ("calendar", "1", {"fit": True})):
        with pytest.raises(ValueError):
            get_family(*args)


def test_base_rejects_falsely_valid_future_information():
    class BrokenFamily(ConsumptionLags):
        def _build(self, isolated):
            result = super()._build(isolated)
            result.values.iloc[:, 0] = 123.0
            result.valid.iloc[:, 0] = True
            result.reasons.iloc[:, 0] = "available"
            return replace(result)

    frame = history()
    targets = pd.DatetimeIndex([frame.loc[200, "period_start_utc"]])
    with pytest.raises(ValueError, match="unavailable at forecast issue"):
        BrokenFamily({"lags": [1]}).build(context(frame, targets, delay=1))


def test_empty_feature_request_has_stable_schema():
    targets = pd.DatetimeIndex([], tz="UTC")
    for family in ("consumption_lags", "calendar"):
        assert get_family(family).build(context(history(), targets)).values.empty


@pytest.mark.parametrize("broken", ["naive_end", "misaligned_start", "late_issue"])
def test_invalid_time_context_is_rejected(broken):
    frame = history()
    targets = pd.DatetimeIndex([frame.loc[200, "period_start_utc"]])
    issues = targets
    if broken == "naive_end":
        frame["period_end_utc"] = frame["period_end_utc"].dt.tz_localize(None)
    elif broken == "misaligned_start":
        frame["period_start_utc"] += pd.Timedelta(minutes=1)
    else:
        issues = targets + pd.Timedelta(hours=1)
    with pytest.raises(ValueError):
        context(frame, targets, issues=issues)