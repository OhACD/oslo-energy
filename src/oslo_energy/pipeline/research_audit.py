"""Read-only research-readiness audits linked to completed cleaning artifacts."""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
from typing import Sequence
from uuid import uuid4

import pandas as pd

from oslo_energy.ingestion.archive import Archive
from oslo_energy.pipeline.cleaning import artifact_checksum, validate_cleaning_run
from oslo_energy.pipeline.research_config import load_research_config, validate_windows
from oslo_energy.transformation.cleaning import MEASUREMENTS, OBSERVATION_COLUMNS
from oslo_energy.transformation.research_audit import ResearchWindow, missing_intervals, profile_window
from oslo_energy.transformation.statnett_normalizer import StatnettNormalizer


AUDIT_VERSION = "1"
SOURCE_CONTEXT_VERSION = "2026-10-13"
SOURCE_INVESTIGATION = Path(__file__).parents[3] / "docs" / "statnett-source-investigation.md"


@dataclass(frozen=True)
class ResearchAuditRun:
    """A published descriptive audit, not an automatic eligibility decision."""

    output_dir: Path
    report: dict


def _timestamp(value: str) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp) or timestamp.tzinfo is None:
        raise ValueError("Audit inputs require timezone-aware timestamps")
    return timestamp.tz_convert("UTC")


def load_cleaned_observations(cleaned_dir: Path, report: dict) -> pd.DataFrame:
    frame = pd.read_csv(cleaned_dir / "observations.csv", dtype=str, keep_default_na=False)
    required = {*OBSERVATION_COLUMNS, *(f"{metric}_original" for metric in MEASUREMENTS)}
    if not required.issubset(frame.columns):
        raise ValueError("Cleaned observations are missing required columns")
    for column in ("period_start_utc", "period_end_utc"):
        frame[column] = pd.to_datetime(frame[column].map(_timestamp), utc=True)
        if not frame[column].eq(frame[column].dt.floor("h")).all():
            raise ValueError("Cleaned periods must identify UTC hour boundaries")
    if not (frame["period_end_utc"] - frame["period_start_utc"]).eq(pd.Timedelta(hours=1)).all():
        raise ValueError("Cleaned periods must last one elapsed hour")
    if frame["period_start_utc"].duplicated().any() or not frame["period_start_utc"].is_monotonic_increasing:
        raise ValueError("Cleaned identities must be unique and UTC ordered")
    if not frame["period_end_utc"].le(_timestamp(report["normalization_report"]["fetched_at_utc"])).all():
        raise ValueError("Cleaned input contains unfinished observations")
    for column in ("period_hours", "source_index"):
        numeric = pd.to_numeric(frame[column], errors="raise")
        if not (numeric.ge(0) & numeric.mod(1).eq(0)).all():
            raise ValueError(f"Invalid cleaned integer column: {column}")
        frame[column] = numeric.astype("int64")
    if not frame["period_hours"].eq(1).all() or frame["source_index"].duplicated().any():
        raise ValueError("Invalid cleaned hourly/source identity")
    local_dates = frame["period_start_utc"].dt.tz_convert("Europe/Oslo").dt.strftime("%Y-%m-%d")
    if not frame["observation_date"].eq(local_dates).all():
        raise ValueError("Cleaned observation dates disagree with Europe/Oslo")
    for metric in (*MEASUREMENTS, *(f"{metric}_original" for metric in MEASUREMENTS)):
        values = pd.to_numeric(frame[metric].replace("", None), errors="raise")
        StatnettNormalizer._measurements(
            [None if cell == "" else float(value) for cell, value in zip(frame[metric], values)], metric,
        )
        frame[metric] = values.astype("float64")
    if len(frame) != report["counts"]["output_observations"]:
        raise ValueError("Cleaned report and observation row counts disagree")
    return frame


def _source_context(window: ResearchWindow) -> list[dict]:
    issues = (
        ("production_export_scaling_interval", "production", "2025-03-18T00:00:00Z", "2025-04-10T00:00:00Z",
         "API/export production disagreement; no confirmed cause or approved rescaling"),
        ("consumption_export_divergence_after_onset", "consumption", "2025-07-09T04:00:00Z", None,
         "Overlap after documented divergence onset; no confirmed end, not proof every hour is erroneous"),
    )
    context = []
    for code, metric, start, end, detail in issues:
        overlap_start = max(window.start_utc, pd.Timestamp(start))
        overlap_end = min(window.end_utc, pd.Timestamp(end)) if end else window.end_utc
        if overlap_end > overlap_start:
            context.append({
                "code": code, "metric": metric, "overlap_start_utc": overlap_start.isoformat(),
                "overlap_end_utc_exclusive": overlap_end.isoformat(),
                "overlap_hour_count": int((overlap_end - overlap_start) / pd.Timedelta(hours=1)),
                "detail": detail, "evidence_version": SOURCE_CONTEXT_VERSION,
                "reference": "docs/statnett-source-investigation.md",
            })
    return context


def validate_research_audit(output_dir: Path) -> dict:
    """Require a complete audit and verify its report against the manifest."""
    manifest = Archive(output_dir / "manifest.json").read()
    if not isinstance(manifest, dict) or manifest.get("schema_version") != AUDIT_VERSION:
        raise ValueError("Incompatible research audit manifest")
    if manifest.get("audit_report_sha256") != artifact_checksum(output_dir / "audit_report.json"):
        raise ValueError("Research audit report checksum mismatch")
    report = Archive(output_dir / "audit_report.json").read()
    if (
        report.get("run_id") != manifest.get("run_id") or report.get("schema_version") != AUDIT_VERSION
        or report.get("training_ready") is not False or report.get("research_eligibility") != "pending_review"
        or report.get("status") != "completed_with_warnings"
        or report.get("configuration") != manifest.get("configuration")
        or report.get("configuration_fingerprint") != manifest.get("configuration_fingerprint")
    ):
        raise ValueError("Inconsistent or unreviewed research audit metadata")
    return manifest


def run_research_audit(
    cleaned_dir: Path, *, windows: Sequence[ResearchWindow],
    output_dir: Path = Path("data/audits/statnett"), top_changes: int = 10,
    absolute_change_threshold: float | None = None, config_path: Path | None = None,
) -> ResearchAuditRun:
    """Verify a frozen cleaned input and publish descriptive checks for explicit windows."""
    ordered = validate_windows(windows)
    shared_config = load_research_config(config_path) if config_path else None
    if shared_config and (shared_config.cleaned_dir != cleaned_dir.resolve() or shared_config.windows != ordered):
        raise ValueError("Audit inputs/windows disagree with shared configuration")
    cleaned_dir, output_dir = cleaned_dir.resolve(), output_dir.resolve()
    if output_dir == cleaned_dir or cleaned_dir in output_dir.parents:
        raise ValueError("Audit output must be outside the cleaned input directory")
    input_manifest_hash = artifact_checksum(cleaned_dir / "manifest.json")
    if shared_config and input_manifest_hash != shared_config.effective["inputs"]["cleaned"]["manifest_sha256"]:
        raise ValueError("Cleaning manifest disagrees with shared configuration")
    manifest = validate_cleaning_run(cleaned_dir)
    cleaning_report = Archive(cleaned_dir / "cleaning_report.json").read()
    if cleaning_report["normalization_report"].get("frequency") != "hourly":
        raise ValueError("Research audit requires hourly cleaned observations")
    observations = load_cleaned_observations(cleaned_dir, cleaning_report)
    profiles = [profile_window(
        observations, window, top_changes=top_changes, absolute_change_threshold=absolute_change_threshold,
    ) for window in ordered]
    captured_through = _timestamp(cleaning_report["normalization_report"]["fetched_at_utc"]).floor("h")
    for profile, window in zip(profiles, ordered):
        profile["known_source_context"] = _source_context(window)
        profile["hours_not_completed_at_capture"] = max(0, int(
            (window.end_utc - max(window.start_utc, captured_through)) / pd.Timedelta(hours=1)
        ))
        values = observations.set_index("period_start_utc")["consumption"].reindex(
            pd.date_range(window.start_utc, window.end_utc, freq="h", inclusive="left")
        )
        profile["zero_consumption_intervals"] = missing_intervals(values.eq(0))
        profile["lowest_consumption_examples"] = [
            {"period_start_utc": timestamp.isoformat(), "consumption": float(value)}
            for timestamp, value in values.dropna().nsmallest(top_changes).items()
        ]
        profile["highest_consumption_examples"] = [
            {"period_start_utc": timestamp.isoformat(), "consumption": float(value)}
            for timestamp, value in values.dropna().nlargest(top_changes).items()
        ]
    configuration = {
        "windows": [{"name": window.name, "from_date": window.from_date.isoformat(),
                     "to_date": window.to_date.isoformat()} for window in ordered],
        "top_changes": top_changes, "absolute_change_threshold": absolute_change_threshold,
        "source_context_version": SOURCE_CONTEXT_VERSION,
    }
    fingerprint = hashlib.sha256(json.dumps(
        configuration, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()
    run_id = f"research_audit_{uuid4().hex}"
    report = {
        "schema_version": AUDIT_VERSION, "run_id": run_id, "status": "completed_with_warnings",
        "training_ready": False, "research_eligibility": "pending_review",
        "cleaning_run_id": manifest["run_id"], "configuration": configuration,
        "configuration_fingerprint": fingerprint,
        "checks": {
            "cleaning_artifact_integrity": "passed", "cleaned_observation_contract": "passed",
            "research_window_coverage": "findings" if any(profile["absent_hour_count"] for profile in profiles) else "passed",
            "consumption_completeness": "findings" if any(profile["consumption_null_count"] for profile in profiles) else "passed",
            "descriptive_statistical_profile": "completed",
            "threshold_screening": "completed" if absolute_change_threshold is not None else "not_run",
            "source_accuracy": "not_run", "forecast_time_availability": "not_run",
        },
        "windows": profiles,
        "source_limitations": {
            "measurement_units": cleaning_report["normalization_report"]["measurement_units"],
            "reference_validation": cleaning_report["normalization_report"]["reference_validation"],
            "cleaning_warnings": cleaning_report["warnings"],
            "unresolved_conditions": cleaning_report["unresolved_conditions"],
            "revision_evidence": "22 distinct completed hours changed in selective October 6-13 captures; no population rate established",
            "source_context_version": SOURCE_CONTEXT_VERSION,
        },
        "review_required": [
            "Approve explicit missing-input and missing-label handling rules before modeling",
            "Investigate material zeros, extremes, abrupt changes, and measurement regime shifts",
            "Review monthly distributions; no automatic change-point or anomaly model was fitted",
            "Do not tune filtering, features, or thresholds against held-out outcomes",
            "Largest changes and extremes are review candidates, not proven errors or exclusion decisions",
            "Confirm forecast issue-time availability assumptions; completed does not mean immediately published",
            "Explicitly approve provisional API-reference research with unresolved source accuracy and revisions",
        ],
        "original_requested_range_coverage": cleaning_report["checks"]["requested_range_coverage"],
        "coverage_scope": "Explicit research windows, not reconstructed original request bounds",
        "label_policy": "No labels or input measurements were filled, removed, or changed",
        "shared_configuration": ({"sha256": shared_config.sha256, "fingerprint": shared_config.fingerprint} if shared_config else None),
    }
    destination = output_dir / run_id
    destination.mkdir(parents=True, exist_ok=False)
    try:
        Archive(destination / "audit_report.json").write(report)
        if artifact_checksum(cleaned_dir / "manifest.json") != input_manifest_hash:
            raise ValueError("Cleaning manifest changed during audit")
        if shared_config and artifact_checksum(shared_config.path) != shared_config.sha256:
            raise ValueError("Shared configuration changed during audit")
        validate_cleaning_run(cleaned_dir)
        Archive(destination / "manifest.json").write({
            "schema_version": AUDIT_VERSION, "run_id": run_id,
            "executed_at_utc": datetime.now(timezone.utc).isoformat(),
            "cleaned_dir": str(cleaned_dir), "cleaning_manifest_sha256": input_manifest_hash,
            "cleaning_output_sha256": manifest["output_sha256"],
            "configuration": configuration, "configuration_fingerprint": fingerprint,
            "audit_report_sha256": artifact_checksum(destination / "audit_report.json"),
            "implementation_sha256": {
                "pipeline/research_audit.py": artifact_checksum(Path(__file__)),
                "transformation/research_audit.py": artifact_checksum(
                    Path(__file__).parents[1] / "transformation" / "research_audit.py"
                ),
            },
            "software_versions": {"python": sys.version, "pandas": pd.__version__},
            "source_investigation_sha256": artifact_checksum(SOURCE_INVESTIGATION) if SOURCE_INVESTIGATION.is_file() else None,
        })
        validate_research_audit(destination)
    except BaseException:
        shutil.rmtree(destination)
        raise
    return ResearchAuditRun(destination, report)