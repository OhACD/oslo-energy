"""Shared observation schema, policy capabilities, context, and results."""

from dataclasses import dataclass, field
from typing import Any, Mapping

import pandas as pd


IDENTITY_COLUMNS = (
    "period_start_utc", "period_end_utc", "observation_date", "period_hours", "source_index",
)
MEASUREMENTS = ("production", "consumption")
OBSERVATION_COLUMNS = (*IDENTITY_COLUMNS, *MEASUREMENTS)


@dataclass(frozen=True)
class QualityRecord:
    """A diagnostic or policy action linked to a row, interval, or dataset."""

    kind: str
    code: str
    severity: str = "info"
    period_start_utc: str | None = None
    period_end_utc: str | None = None
    metric: str | None = None
    action: str | None = None
    detail: str = ""


@dataclass(frozen=True)
class PolicyCapabilities:
    """Declare permitted analytical changes independently of policy behavior."""

    can_exclude: bool = False
    can_modify: bool = False
    can_synthesize: bool = False


@dataclass(frozen=True)
class CleaningContext:
    """Validated normalized data, independent findings, and execution lineage."""

    observations: pd.DataFrame
    normalization_report: Mapping[str, Any]
    findings: tuple[QualityRecord, ...] = ()
    dataset_id: str = ""
    provenance: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CleaningResult:
    """Analytical data and explicit decisions, never a training certification."""

    observations: pd.DataFrame
    findings: tuple[QualityRecord, ...]
    actions: tuple[QualityRecord, ...]
    warnings: tuple[str, ...] = ()
    unresolved_conditions: tuple[str, ...] = ()

    @property
    def counts(self) -> dict[str, int]:
        return {
            "output_observations": len(self.observations), "diagnostic_findings": len(self.findings),
            "policy_actions": len(self.actions),
            **{f"{metric}_missing_count": int(self.observations[metric].isna().sum()) for metric in MEASUREMENTS},
        }