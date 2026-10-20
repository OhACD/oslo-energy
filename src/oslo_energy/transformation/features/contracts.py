"""Feature results and a measurement accessor that enforces issue-time availability."""

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class FeatureResult:
    """Timestamp-aligned values, validity, reasons, and measurement availability."""

    values: pd.DataFrame
    valid: pd.DataFrame
    reasons: pd.DataFrame
    available_at: pd.DataFrame


class CausalHistory:
    """Expose lagged measurements only when completed and available at issue time."""

    def __init__(
        self, observations: pd.DataFrame, targets: pd.DatetimeIndex,
        issue_times: pd.DatetimeIndex, publication_delay_hours: int = 0,
    ):
        if type(publication_delay_hours) is not int or publication_delay_hours < 0:
            raise ValueError("publication_delay_hours must be a nonnegative integer")
        for times in (targets, issue_times):
            if times.tz is None or times.hasnans or not times.equals(times.floor("h")):
                raise ValueError("Feature times must be aware hour boundaries")
        if len(targets) != len(issue_times) or targets.duplicated().any() or not targets.is_monotonic_increasing:
            raise ValueError("Feature target/issue times must be aligned and uniquely ordered")
        if (issue_times > targets).any():
            raise ValueError("A forecast cannot be issued after its target starts")
        source = observations.set_index("period_start_utc")[["period_end_utc", "consumption"]].copy(deep=True)
        if not isinstance(source.index, pd.DatetimeIndex) or source.index.tz is None or source.index.hasnans:
            raise ValueError("History must have aware UTC period identities")
        if source.index.duplicated().any() or not source.index.is_monotonic_increasing:
            raise ValueError("History identities must be unique and ordered")
        if not source.index.equals(source.index.floor("h")) or not isinstance(source["period_end_utc"].dtype, pd.DatetimeTZDtype):
            raise ValueError("History boundaries must be aware and hour aligned")
        ends = pd.to_datetime(source["period_end_utc"], utc=True)
        if not ends.eq(source.index + pd.Timedelta(hours=1)).all():
            raise ValueError("History must contain one-hour periods")
        self._data = source
        self._targets = targets.tz_convert("UTC").rename("target_start_utc")
        self._issues = issue_times.tz_convert("UTC")
        self._delay = publication_delay_hours

    @property
    def targets(self) -> pd.DatetimeIndex:
        return self._targets.copy()

    @property
    def issue_times(self) -> pd.DatetimeIndex:
        return self._issues.copy()

    def copy(self) -> "CausalHistory":
        return CausalHistory(self._data.reset_index(), self._targets, self._issues, self._delay)

    def lag(self, hours: int) -> FeatureResult:
        if type(hours) is not int or hours <= 0:
            raise ValueError("Measurement lags must be strictly positive integer hours")
        source_times = self._targets - pd.Timedelta(hours=hours)
        selected = self._data.reindex(source_times).copy()
        selected.index = self._targets
        present = pd.Series(source_times.isin(self._data.index), index=self._targets)
        available = pd.to_datetime(selected["period_end_utc"], utc=True) + pd.Timedelta(hours=self._delay)
        permitted = available.le(pd.Series(self._issues, index=self._targets))
        valid = present & permitted & selected["consumption"].notna()
        reasons = pd.Series("available", index=self._targets)
        reasons.loc[~present] = "absent_source_hour"
        if len(self._data):
            reasons.loc[~present & (source_times < self._data.index.min())] = "insufficient_history"
        reasons.loc[present & selected["consumption"].isna()] = "missing_measurement"
        reasons.loc[present & ~permitted] = "not_available_at_issue"
        name = f"consumption_lag_{hours}h"
        return FeatureResult(
            selected["consumption"].where(valid).astype("float64").to_frame(name), valid.to_frame(name),
            reasons.to_frame(name), available.to_frame(name),
        )


@dataclass(frozen=True)
class FeatureContext:
    """Families receive a restricted history accessor, not a target-label table."""

    history: CausalHistory