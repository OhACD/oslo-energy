"""Non-mutating research-window coverage and descriptive consumption profiles."""

from dataclasses import dataclass
from datetime import date
import math
import re

import pandas as pd


@dataclass(frozen=True)
class ResearchWindow:
    """An explicitly named inclusive Europe/Oslo date interval."""

    name: str
    from_date: date
    to_date: date

    def __post_init__(self):
        if not re.fullmatch(r"[a-z][a-z0-9_]*", self.name):
            raise ValueError("Window names must use lowercase letters, digits, and underscores")
        if self.from_date > self.to_date:
            raise ValueError("Research window dates must be ordered")

    @property
    def start_utc(self) -> pd.Timestamp:
        return pd.Timestamp(self.from_date, tz="Europe/Oslo").tz_convert("UTC")

    @property
    def end_utc(self) -> pd.Timestamp:
        end = pd.Timestamp(self.to_date, tz="Europe/Oslo") + pd.DateOffset(days=1)
        return end.tz_convert("UTC")


def missing_intervals(mask: pd.Series) -> list[dict]:
    """Describe consecutive true UTC hours without manufacturing source rows."""
    positions = mask.index[mask]
    intervals = []
    for timestamp in positions:
        if intervals and intervals[-1]["end_utc"] == timestamp.isoformat():
            intervals[-1]["end_utc"] = (timestamp + pd.Timedelta(hours=1)).isoformat()
            intervals[-1]["hour_count"] += 1
        else:
            intervals.append({
                "start_utc": timestamp.isoformat(),
                "end_utc": (timestamp + pd.Timedelta(hours=1)).isoformat(), "hour_count": 1,
            })
    return intervals


def _distribution(values: pd.Series) -> dict:
    valid = values.dropna()
    if valid.empty:
        return {"count": 0, "minimum": None, "maximum": None, "mean": None,
                "standard_deviation": None, "quantiles": {}}
    return {
        "count": len(valid), "minimum": float(valid.min()), "maximum": float(valid.max()),
        "mean": float(valid.mean()), "standard_deviation": float(valid.std(ddof=0)),
        "quantiles": {str(quantile): float(value) for quantile, value in
                      valid.quantile([0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99]).items()},
    }


def profile_window(
    observations: pd.DataFrame, window: ResearchWindow, *, top_changes: int = 10,
    absolute_change_threshold: float | None = None,
) -> dict:
    """Measure a window on its expected grid; no fitted anomaly rule or filtering."""
    if type(top_changes) is not int or top_changes < 1:
        raise ValueError("top_changes must be a positive integer")
    if absolute_change_threshold is not None and (
        isinstance(absolute_change_threshold, bool)
        or not isinstance(absolute_change_threshold, (int, float))
        or not math.isfinite(absolute_change_threshold) or absolute_change_threshold <= 0
    ):
        raise ValueError("absolute_change_threshold must be finite and positive")
    expected = pd.date_range(window.start_utc, window.end_utc, freq="h", inclusive="left")
    indexed = observations.set_index("period_start_utc")
    if indexed.index.duplicated().any():
        raise ValueError("Audit observations must have unique UTC identities")
    selected = indexed.reindex(expected)
    present = pd.Series(expected.isin(indexed.index), index=expected)
    consumption = selected["consumption"]
    absent, null = ~present, present & consumption.isna()
    unavailable = consumption.isna()
    change = consumption.diff()
    largest = change.abs().dropna().nlargest(top_changes, keep="first")
    review = []
    for timestamp, magnitude in largest.items():
        review.append({
            "period_start_utc": timestamp.isoformat(),
            "previous_period_start_utc": (timestamp - pd.Timedelta(hours=1)).isoformat(),
            "previous_consumption": float(consumption.loc[timestamp - pd.Timedelta(hours=1)]),
            "consumption": float(consumption.loc[timestamp]),
            "change": float(change.loc[timestamp]), "absolute_change": float(magnitude),
        })
    missing = missing_intervals(unavailable)
    months = pd.Series(expected.tz_convert("Europe/Oslo").strftime("%Y-%m"), index=expected)
    by_month = []
    for month in months.unique():
        members = months.eq(month)
        by_month.append({
            "local_month": month, "expected_hour_count": int(members.sum()),
            "absent_hour_count": int(absent[members].sum()),
            "consumption_null_count": int(null[members].sum()),
            "available_consumption_count": int(consumption[members].notna().sum()),
            "consumption": _distribution(consumption[members]),
        })
    return {
        "name": window.name, "from_date": window.from_date.isoformat(), "to_date": window.to_date.isoformat(),
        "timezone": "Europe/Oslo", "start_utc": window.start_utc.isoformat(),
        "end_utc_exclusive": window.end_utc.isoformat(),
        "expected_hour_count": len(expected), "present_hour_count": int(present.sum()),
        "absent_hour_count": int(absent.sum()), "consumption_null_count": int(null.sum()),
        "available_consumption_count": int(consumption.notna().sum()),
        "consumption_availability_rate": float(consumption.notna().mean()),
        "first_expected_hour_present": bool(present.iloc[0]),
        "last_expected_hour_present": bool(present.iloc[-1]),
        "absent_intervals": missing_intervals(absent), "null_consumption_intervals": missing_intervals(null),
        "unavailable_consumption_intervals": missing,
        "longest_unavailable_consumption_hours": max((item["hour_count"] for item in missing), default=0),
        "consumption": _distribution(consumption), "zero_consumption_count": int(consumption.eq(0).sum()),
        "hourly_change": _distribution(change), "largest_hourly_changes": review,
        "absolute_change_threshold": absolute_change_threshold,
        "threshold_exceedance_count": (
            int(change.abs().gt(absolute_change_threshold).sum()) if absolute_change_threshold is not None else None
        ),
        "by_local_month": by_month,
    }