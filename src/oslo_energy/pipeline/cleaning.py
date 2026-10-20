"""Validate normalized hourly artifacts and derive independent cleaning diagnostics."""

from collections import Counter
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
from typing import Any
from uuid import uuid4

import pandas as pd

from oslo_energy.ingestion.archive import Archive
from oslo_energy.pipeline.normalization import request_coverage
from oslo_energy.pipeline.planning import DateRange
from oslo_energy.transformation.cleaning import (
    MEASUREMENTS, OBSERVATION_COLUMNS, CleaningContext, CleaningResult, QualityRecord, get_policy,
)
from oslo_energy.transformation.statnett_normalizer import (
    NormalizedStatnettData, StatnettNormalizer, StatnettQualityReport,
)


@dataclass(frozen=True)
class CleaningInput:
    """Validated policy context and the checks used to establish it."""

    context: CleaningContext
    checks: dict[str, str]
    artifacts: tuple[Path, ...]


def _aware_timestamp(value: str) -> pd.Timestamp:
    try:
        timestamp = pd.Timestamp(value)
        if pd.isna(timestamp) or timestamp.tzinfo is None:
            raise ValueError(value)
        return timestamp.tz_convert("UTC")
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"Expected a timezone-aware timestamp: {value}") from exc


def _iso_date(value: str) -> date:
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("Expected a YYYY-MM-DD date")
    return parsed


def _read_observations(path: Path, fetched_at: pd.Timestamp, *, incomplete: bool) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    if set(frame.columns) != set(OBSERVATION_COLUMNS):
        raise ValueError(f"Incompatible observation columns in {path.name}")
    for column in ("period_start_utc", "period_end_utc"):
        frame[column] = pd.to_datetime(frame[column].map(_aware_timestamp), utc=True)
        if not frame[column].eq(frame[column].dt.floor("h")).all():
            raise ValueError("Periods must use UTC hour boundaries")
    if not (frame["period_end_utc"] - frame["period_start_utc"]).eq(pd.Timedelta(hours=1)).all():
        raise ValueError("Periods must last exactly one hour")
    if frame["period_start_utc"].duplicated().any() or frame["source_index"].duplicated().any():
        raise ValueError("Duplicate observation identity or source index")
    if not frame["period_start_utc"].is_monotonic_increasing:
        raise ValueError("Observations must be ordered by UTC identity")
    for column in ("period_hours", "source_index"):
        numeric = pd.to_numeric(frame[column], errors="raise")
        if not numeric.map(lambda value: math.isfinite(value) and value >= 0 and value == int(value)).all():
            raise ValueError(f"{column} must contain nonnegative integers")
        frame[column] = numeric.astype("int64")
    if frame["source_index"].duplicated().any():
        raise ValueError("Duplicate numeric source index")
    if not frame["period_hours"].eq(1).all():
        raise ValueError("Only hourly observations are supported")
    if not frame["source_index"].is_monotonic_increasing:
        raise ValueError("Source indices must preserve source ordering")
    frame["observation_date"] = frame["observation_date"].map(_iso_date)
    local_dates = frame["period_start_utc"].dt.tz_convert("Europe/Oslo").dt.date
    if not frame["observation_date"].eq(local_dates).all():
        raise ValueError("Observation dates must match Europe/Oslo period starts")
    for metric in MEASUREMENTS:
        values = pd.to_numeric(frame[metric].replace("", None), errors="raise")
        StatnettNormalizer._measurements(
            [None if value == "" else float(number) for value, number in zip(frame[metric], values)], metric,
        )
        frame[metric] = values.astype("float64")
    completed = frame["period_end_utc"] <= fetched_at
    if incomplete:
        if not ((frame["period_start_utc"] <= fetched_at) & ~completed).all():
            raise ValueError("Incomplete context contains a completed or future period")
    elif not completed.all():
        raise ValueError("Historical input contains an unfinished period")
    return frame


def _validate_report(report: Any) -> tuple[StatnettQualityReport, pd.Timestamp]:
    required = {field.name for field in fields(StatnettQualityReport)}
    if not isinstance(report, dict) or not (required | {"source_archive"}).issubset(report):
        raise ValueError("Normalization report is missing required metadata")
    if report["frequency"] != "hourly" or type(report["period_tick_ms"]) is not int or report["period_tick_ms"] != 3600000:
        raise ValueError("Cleaning accepts hourly normalized runs only")
    if report["timezone"] != "Europe/Oslo" or report["geographic_scope"] != "Norway":
        raise ValueError("Incompatible normalized timezone or geographic scope")
    if report["training_ready"] is not False:
        raise ValueError("Normalized input must retain training_ready=false")
    for name in ("source_archive", "measurement_units", "reference_validation", "fetched_at_utc"):
        if not isinstance(report[name], str) or not report[name].strip():
            raise ValueError(f"Missing normalization provenance: {name}")
    for name in required:
        if name.endswith("_count") and (type(report[name]) is not int or report[name] < 0):
            raise ValueError(f"Invalid normalization count: {name}")
    total = report["observation_count"]
    if total != sum(report[name] for name in ("historical_count", "incomplete_count", "forecast_count")):
        raise ValueError("Normalization partition counts contradict observation_count")
    if report["raw_slot_count"] != total or report["padding_count"] != 0 or report["padding_dates"] != []:
        raise ValueError("Hourly normalization cannot contain daily padding")
    if report["duplicate_count"] != 0:
        raise ValueError("Normalized run reports duplicate identities")
    for metric in MEASUREMENTS:
        count, rate = report[f"{metric}_missing_count"], report[f"{metric}_missing_rate"]
        if (
            count > total or isinstance(rate, bool) or not isinstance(rate, (int, float))
            or not math.isfinite(rate) or not math.isclose(rate, count / total if total else 0, abs_tol=1e-12)
        ):
            raise ValueError(f"Contradictory normalization missingness for {metric}")
    if _iso_date(report["source_start_date"]) > _iso_date(report["source_end_date"]):
        raise ValueError("Normalization source dates are reversed")
    return StatnettQualityReport(**{name: report[name] for name in required}), _aware_timestamp(report["fetched_at_utc"])


def load_cleaning_input(input_dir: Path) -> CleaningInput:
    """Establish required contracts without collecting data or accessing a database."""
    input_dir = input_dir.resolve()
    report_path, observations_path = input_dir / "quality.json", input_dir / "historical_candidates.csv"
    artifacts = [observations_path, report_path]
    incomplete_path = input_dir / "incomplete.csv"
    if incomplete_path.exists():
        artifacts.append(incomplete_path)
    input_hashes = {path.name: artifact_checksum(path) for path in artifacts}
    report = Archive(report_path).read()
    quality, fetched_at = _validate_report(report)
    historical = _read_observations(observations_path, fetched_at, incomplete=False)
    if len(historical) != quality.historical_count:
        raise ValueError("Historical row count contradicts normalization report")
    checks = {"input_contract": "passed", "observation_identity": "passed", "measurement_domain": "passed",
              "internal_continuity": "passed", "requested_range_coverage": "not_run",
              "incomplete_context": "not_run", "statistical_behavior": "not_run",
              "source_accuracy": "not_run", "whole_run_missingness": "not_run"}
    incomplete = historical.iloc[:0].copy()
    if incomplete_path.exists():
        incomplete = _read_observations(incomplete_path, fetched_at, incomplete=True)
        if len(incomplete) != quality.incomplete_count:
            raise ValueError("Incomplete row count contradicts normalization report")
        checks["incomplete_context"] = "passed"
    available = pd.concat([historical, incomplete], ignore_index=True)
    if available["period_start_utc"].duplicated().any() or available["source_index"].duplicated().any():
        raise ValueError("Historical and incomplete identities overlap")
    if not available.empty:
        dates = available["observation_date"]
        if dates.min() < _iso_date(quality.source_start_date) or dates.max() > _iso_date(quality.source_end_date):
            raise ValueError("Observation dates contradict reported source dates")
    for metric in MEASUREMENTS:
        missing = int(available[metric].isna().sum())
        expected = getattr(quality, f"{metric}_missing_count")
        if missing > expected or (len(available) == quality.observation_count and missing != expected):
            raise ValueError(f"Available measurements contradict reported missingness for {metric}")
    if len(available) == quality.observation_count:
        checks["whole_run_missingness"] = "passed"
    findings = []
    for metric in MEASUREMENTS:
        for row in historical.loc[historical[metric].isna()].itertuples(index=False):
            findings.append(QualityRecord(
                "diagnostic", "missing_measurement", "warning", row.period_start_utc.isoformat(),
                row.period_end_utc.isoformat(), metric,
            ))
    starts = pd.DatetimeIndex(historical["period_start_utc"])
    expected = pd.DatetimeIndex([], tz="UTC")
    if len(starts):
        expected = pd.date_range(starts.min(), starts.max(), freq="h")
    if len(expected.difference(starts)):
        checks["internal_continuity"] = "findings"
    if "request_coverage" in report:
        coverage = report["request_coverage"]
        if not isinstance(coverage, dict):
            raise ValueError("Invalid request_coverage metadata")
        try:
            requested = DateRange(_iso_date(coverage["from_date"]), _iso_date(coverage["to_date"]))
            normalized = NormalizedStatnettData(available, historical, incomplete, historical.iloc[:0], quality)
            computed = request_coverage(normalized, requested, fetched_at.to_pydatetime())
        except (KeyError, TypeError) as exc:
            raise ValueError("Incomplete request_coverage metadata") from exc
        for name, value in asdict(computed).items():
            if name not in coverage or type(coverage[name]) is not type(value) or coverage[name] != value:
                raise ValueError(f"Contradictory request_coverage: {name}")
        start = pd.Timestamp(requested.from_date, tz="Europe/Oslo").tz_convert("UTC")
        end = pd.Timestamp(requested.to_date, tz="Europe/Oslo") + pd.DateOffset(days=1)
        end = min(end.tz_convert("UTC"), fetched_at.floor("h"))
        expected = pd.date_range(start, end, freq="h", inclusive="left")
        checks["requested_range_coverage"] = "findings" if computed.unresolved else "passed"
    missing_hours = expected.difference(starts)
    for timestamp in missing_hours:
        findings.append(QualityRecord(
            "diagnostic", "absent_hour", "warning", timestamp.isoformat(),
            (timestamp + pd.Timedelta(hours=1)).isoformat(), detail="No source observation exists",
        ))
    for code, detail in (
        ("source_units_unvalidated", quality.measurement_units),
        ("source_reference_unresolved", quality.reference_validation),
    ):
        findings.append(QualityRecord("diagnostic", code, "warning", detail=detail))
    return CleaningInput(
        CleaningContext(historical, report, tuple(findings), str(input_dir),
                        {"input_dir": str(input_dir), "input_sha256": input_hashes}),
        checks, tuple(artifacts),
    )


SCHEMA_VERSION = "1"
OUTPUT_ARTIFACTS = ("observations.csv", "row_quality.csv", "cleaning_report.json")


@dataclass(frozen=True)
class CleaningRun:
    """Completed artifact location and the operational cleaning outcome."""

    output_dir: Path
    report: dict[str, Any]


def artifact_checksum(path: Path) -> str:
    """Hash artifact bytes without loading large files into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as artifact:
        for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fingerprint(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _code_version() -> dict[str, Any]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).parent,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit = None
    source_root = Path(__file__).parents[1]
    paths = sorted(source_root.rglob("*.py"))
    return {"git_commit": commit, "package_source_sha256": {
        str(path.relative_to(source_root)): artifact_checksum(path) for path in paths
    }}


def _result_counts(context: CleaningContext, result: CleaningResult) -> dict[str, int]:
    source = context.observations.set_index("period_start_utc")
    target = result.observations.set_index("period_start_utc")
    common = source.index.intersection(target.index)
    changed = 0
    for metric in MEASUREMENTS:
        before, after = source.loc[common, metric], target.loc[common, metric]
        changed += int((~(before.eq(after) | (before.isna() & after.isna()))).sum())
    return {
        **result.counts, "input_observations": len(source),
        "excluded_observations": len(source.index.difference(target.index)),
        "synthetic_observations": len(target.index.difference(source.index)),
        "changed_measurements": changed,
    }


def validate_cleaning_run(output_dir: Path) -> dict[str, Any]:
    """Require the completion manifest and verify all declared output checksums."""
    manifest = Archive(output_dir / "manifest.json").read()
    if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Incompatible cleaning manifest")
    checksums = manifest.get("output_sha256")
    if not isinstance(checksums, dict) or set(checksums) != set(OUTPUT_ARTIFACTS):
        raise ValueError("Incomplete cleaning output manifest")
    for name in OUTPUT_ARTIFACTS:
        if checksums[name] != artifact_checksum(output_dir / name):
            raise ValueError(f"Cleaning artifact checksum mismatch: {name}")
    report = Archive(output_dir / "cleaning_report.json").read()
    if (
        report.get("status") not in ("success", "completed_with_warnings")
        or report.get("training_ready") is not False
        or report.get("run_id") != manifest.get("run_id")
        or report.get("schema_version") != SCHEMA_VERSION
    ):
        raise ValueError("Cleaning run is not a completed, restricted analytical output")
    return manifest


def run_cleaning(
    input_dir: Path, *, output_dir: Path = Path("data/cleaned/statnett"),
    policy_id: str = "preserve_and_flag", configuration: dict[str, Any] | None = None,
) -> CleaningRun:
    """Run a registered policy offline and publish a uniquely identified derived run.

    The manifest is written last. Any failed write or validation removes only
    this execution's new directory; original artifacts are never changed.
    """
    policy = get_policy(policy_id, configuration)
    effective = {
        "policy_id": policy.policy_id, "policy_version": policy.version,
        "configuration": policy.configuration, "capabilities": asdict(policy.capabilities),
    }
    fingerprint = _fingerprint(effective)
    input_dir, output_dir = input_dir.resolve(), output_dir.resolve()
    if output_dir == input_dir or input_dir in output_dir.parents:
        raise ValueError("Cleaning output must be outside the normalized input directory")
    loaded = load_cleaning_input(input_dir)
    input_hashes = loaded.context.provenance["input_sha256"]
    run_id = f"{policy.policy_id}_{uuid4().hex}"
    context = CleaningContext(
        loaded.context.observations, loaded.context.normalization_report, loaded.context.findings,
        loaded.context.dataset_id, {"input_dir": str(input_dir), "input_sha256": input_hashes, "run_id": run_id},
    )
    result = policy.apply(context)
    archive_path = Path(context.normalization_report["source_archive"])
    archive_hash = artifact_checksum(archive_path) if archive_path.is_file() else None
    warnings = list(dict.fromkeys([
        *(record.detail or record.code for record in result.findings if record.severity == "warning"),
        *result.warnings,
        *(["Referenced source archive checksum unavailable"] if archive_hash is None else []),
    ]))
    unresolved = list(dict.fromkeys([
        "measurement_units", "reference_validation", "forecast_time_availability",
        *result.unresolved_conditions,
    ]))
    report = {
        "schema_version": SCHEMA_VERSION, "run_id": run_id,
        "status": "completed_with_warnings" if warnings or unresolved else "success",
        "training_ready": False, "input_dir": str(input_dir), "policy": effective,
        "configuration_fingerprint": fingerprint, "counts": _result_counts(context, result),
        "findings_by_code": dict(sorted(Counter(record.code for record in result.findings).items())),
        "findings_by_severity": dict(sorted(Counter(record.severity for record in result.findings).items())),
        "actions_by_code": dict(sorted(Counter(record.code for record in result.actions).items())),
        "checks": {**loaded.checks, "policy_output": "passed"},
        "warnings": warnings, "unresolved_conditions": unresolved,
        "normalization_report": dict(context.normalization_report),
    }
    destination = output_dir / run_id
    destination.mkdir(parents=True, exist_ok=False)
    try:
        result.observations.to_csv(destination / "observations.csv", index=False)
        records = [asdict(record) for record in (*result.findings, *result.actions)]
        pd.DataFrame(records, columns=[field.name for field in fields(QualityRecord)]).to_csv(
            destination / "row_quality.csv", index=False,
        )
        Archive(destination / "cleaning_report.json").write(report)
        if input_hashes != {path.name: artifact_checksum(path) for path in loaded.artifacts}:
            raise ValueError("Normalized inputs changed during cleaning execution")
        Archive(destination / "manifest.json").write({
            "schema_version": SCHEMA_VERSION, "run_id": run_id,
            "executed_at_utc": datetime.now(timezone.utc).isoformat(),
            "input_dir": str(input_dir), "input_sha256": input_hashes,
            "output_sha256": {name: artifact_checksum(destination / name) for name in OUTPUT_ARTIFACTS},
            "source_archive": {"path": str(archive_path), "sha256": archive_hash,
                               "status": "available" if archive_hash else "unavailable"},
            "policy": effective, "configuration_fingerprint": fingerprint,
            "seed": None, "code_version": _code_version(),
        })
        validate_cleaning_run(destination)
    except BaseException:
        shutil.rmtree(destination)
        raise
    return CleaningRun(destination, report)