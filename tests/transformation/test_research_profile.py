"""Check explicit research grids, missingness, DST, and gap-aware statistics."""

from datetime import date

import pandas as pd
import pytest

from oslo_energy.transformation.research_audit import ResearchWindow, profile_window


def observations(start, values):
    return pd.DataFrame({
        "period_start_utc": pd.date_range(start, periods=len(values), freq="h", tz="UTC"),
        "consumption": pd.Series(values, dtype="float64"),
    })


def test_explicit_grid_reports_missing_endpoints_and_nulls_separately():
    frame = observations("2025-01-01T00:00:00", [1, None, 3])
    original = frame.copy(deep=True)
    report = profile_window(frame, ResearchWindow("train", date(2025, 1, 1), date(2025, 1, 1)))
    assert report["expected_hour_count"] == 24
    assert report["present_hour_count"] == 3
    assert report["absent_hour_count"] == 21
    assert report["consumption_null_count"] == 1
    assert report["available_consumption_count"] == 2
    assert report["first_expected_hour_present"] is False
    assert report["last_expected_hour_present"] is False
    assert report["hourly_change"]["count"] == 0
    pd.testing.assert_frame_equal(frame, original)


@pytest.mark.parametrize("day, hours", [(date(2025, 3, 30), 23), (date(2025, 10, 26), 25)])
def test_dst_windows_use_elapsed_utc_hours(day, hours):
    window = ResearchWindow("test", day, day)
    frame = observations(window.start_utc, [10] * hours)
    report = profile_window(frame, window)
    assert report["expected_hour_count"] == hours
    assert report["absent_hour_count"] == 0
    assert report["first_expected_hour_present"] is True
    assert report["last_expected_hour_present"] is True


def test_missing_rows_do_not_compress_hourly_differences():
    frame = observations("2024-12-31T23:00:00", [1, 100, 2, 4]).drop(index=1)
    report = profile_window(frame, ResearchWindow("train", date(2025, 1, 1), date(2025, 1, 1)))
    assert report["hourly_change"]["count"] == 1
    assert report["largest_hourly_changes"][0]["change"] == 2
    assert report["threshold_exceedance_count"] is None


def test_adjacent_null_and_absent_hour_form_one_unavailable_stretch():
    frame = observations("2024-12-31T23:00:00", [1] * 24)
    frame.loc[4, "consumption"] = float("nan")
    frame = frame.drop(index=5)
    report = profile_window(frame, ResearchWindow("test", date(2025, 1, 1), date(2025, 1, 1)))
    assert report["longest_unavailable_consumption_hours"] == 2
    assert report["consumption_null_count"] == report["absent_hour_count"] == 1
    assert report["by_local_month"][0]["available_consumption_count"] == 22


def test_empty_window_is_described_not_approved():
    frame = observations("2025-01-01T00:00:00", [])
    report = profile_window(frame, ResearchWindow("test", date(2025, 1, 1), date(2025, 1, 1)))
    assert report["absent_hour_count"] == 24
    assert report["consumption"]["minimum"] is None
    assert report["consumption_availability_rate"] == 0
    assert report["longest_unavailable_consumption_hours"] == 24


def test_threshold_is_explicit_and_never_fitted():
    frame = observations("2024-12-31T23:00:00", [0, 1, 100, 101])
    report = profile_window(
        frame, ResearchWindow("train", date(2025, 1, 1), date(2025, 1, 1)),
        top_changes=1, absolute_change_threshold=50,
    )
    assert report["threshold_exceedance_count"] == 1
    assert report["zero_consumption_count"] == 1
    assert len(report["largest_hourly_changes"]) == 1


@pytest.mark.parametrize("settings", [{"top_changes": 0}, {"top_changes": True},
    {"absolute_change_threshold": -1}, {"absolute_change_threshold": float("inf")}])
def test_invalid_profile_configuration_fails(settings):
    with pytest.raises(ValueError):
        profile_window(observations("2025-01-01", [1]), ResearchWindow("test", date(2025, 1, 1), date(2025, 1, 1)), **settings)


def test_invalid_window_fails():
    with pytest.raises(ValueError):
        ResearchWindow("test", date(2025, 2, 1), date(2025, 1, 1))
    with pytest.raises(ValueError):
        ResearchWindow("../test", date(2025, 1, 1), date(2025, 1, 1))