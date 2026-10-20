"""Publish causal feature matrices separately from labels and research metadata."""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import shutil
import sys
from uuid import uuid4

import pandas as pd

from oslo_energy.ingestion.archive import Archive
from oslo_energy.pipeline.cleaning import artifact_checksum, validate_cleaning_run
from oslo_energy.pipeline.research_audit import load_cleaned_observations, validate_research_audit
from oslo_energy.pipeline.research_config import ResearchConfig, load_research_config
from oslo_energy.transformation.features import CausalHistory, FeatureContext, get_family


FEATURE_SCHEMA_VERSION = "1"
FEATURE_ARTIFACTS = ("features.csv", "examples.csv", "feature_quality.csv", "feature_report.json")


@dataclass(frozen=True)
class FeatureRun:
    output_dir: Path
    report: dict


def _verify_inputs(config: ResearchConfig) -> tuple[dict, dict]:
    inputs = config.effective["inputs"]
    if config.audit_dir is None:
        raise ValueError("Feature generation requires matching audit evidence")
    for name, directory in (("cleaned", config.cleaned_dir), ("audit", config.audit_dir)):
        if artifact_checksum(directory / "manifest.json") != inputs[name]["manifest_sha256"]:
            raise ValueError(f"{name} manifest does not match shared configuration")
    cleaning = validate_cleaning_run(config.cleaned_dir)
    audit = validate_research_audit(config.audit_dir)
    if audit["cleaning_manifest_sha256"] != inputs["cleaned"]["manifest_sha256"] or audit["cleaning_output_sha256"] != cleaning["output_sha256"]:
        raise ValueError("Audit refers to a different cleaned snapshot")
    audit_report = Archive(config.audit_dir / "audit_report.json").read()
    if audit_report["configuration"]["windows"] != config.effective["windows"]:
        raise ValueError("Audit windows do not match the configured split windows")
    return cleaning, audit_report


def validate_feature_run(output_dir: Path) -> dict:
    """Verify completion and integrity before any downstream data access."""
    manifest = Archive(output_dir / "manifest.json").read()
    if not isinstance(manifest, dict) or manifest.get("schema_version") != FEATURE_SCHEMA_VERSION:
        raise ValueError("Incompatible feature manifest")
    hashes = manifest.get("output_sha256")
    if not isinstance(hashes, dict) or set(hashes) != set(FEATURE_ARTIFACTS):
        raise ValueError("Incomplete feature manifest")
    for name, checksum in hashes.items():
        if artifact_checksum(output_dir / name) != checksum:
            raise ValueError(f"Feature artifact checksum mismatch: {name}")
    report = Archive(output_dir / "feature_report.json").read()
    declared_columns = []
    for specification in report["effective_configuration"]["features"]["families"]:
        declared_columns.extend(get_family(
            specification["id"], specification["version"], specification["configuration"],
        ).columns)
    if (
        report.get("schema_version") != FEATURE_SCHEMA_VERSION or report.get("run_id") != manifest.get("run_id")
        or report.get("training_ready") is not False or report.get("research_eligibility") != "pending_review"
        or report.get("configuration_fingerprint") != manifest.get("configuration_fingerprint")
        or report.get("feature_columns") != manifest.get("feature_columns")
        or report.get("feature_columns") != declared_columns
        or len(set(declared_columns)) != len(declared_columns)
    ):
        raise ValueError("Inconsistent feature run metadata")
    return manifest


def load_feature_matrix(output_dir: Path, *, split: str | None = None) -> pd.DataFrame:
    """Return only declared X columns, never labels, quality flags, or split metadata.

    Missing rows are retained. Model-specific eligibility and training-only
    fitted preprocessing remain explicit downstream responsibilities.
    """
    manifest = validate_feature_run(output_dir)
    frame = pd.read_csv(output_dir / "features.csv", float_precision="round_trip")
    expected = ["target_start_utc", *manifest["feature_columns"]]
    if frame.columns.tolist() != expected:
        raise ValueError("Unexpected columns in feature matrix")
    frame.index = pd.to_datetime(frame.pop("target_start_utc"), utc=True)
    if frame.index.hasnans or frame.index.duplicated().any() or not frame.index.is_monotonic_increasing:
        raise ValueError("Invalid feature matrix timestamp identity")
    if split is not None:
        examples = pd.read_csv(output_dir / "examples.csv", usecols=["target_start_utc", "split"])
        if split not in set(examples["split"]):
            raise ValueError(f"Unknown split: {split}")
        selected = pd.DatetimeIndex(pd.to_datetime(examples.loc[examples["split"] == split, "target_start_utc"], utc=True))
        frame = frame.loc[selected]
    return frame.astype("float64")


def run_features(config_path: Path, *, output_dir: Path = Path("data/features/statnett")) -> FeatureRun:
    """Generate stateless, sequential one-hour forecast features from frozen inputs."""
    config = load_research_config(config_path, require_audit=True)
    output_dir = output_dir.resolve()
    for input_root in (config.cleaned_dir, config.audit_dir):
        if output_dir == input_root or input_root in output_dir.parents:
            raise ValueError("Feature outputs must be outside input/configuration directories")
    experiment_runs = config.path.parent / "runs"
    if output_dir == config.path.parent or (
        config.path.parent in output_dir.parents and output_dir != experiment_runs and experiment_runs not in output_dir.parents
    ):
        raise ValueError("Feature outputs inside an experiment must use runs/; otherwise stay outside configuration directories")
    cleaning_manifest, audit_report = _verify_inputs(config)
    cleaning_report = Archive(config.cleaned_dir / "cleaning_report.json").read()
    observations = load_cleaned_observations(config.cleaned_dir, cleaning_report)
    grids = [pd.date_range(window.start_utc, window.end_utc, freq="h", inclusive="left") for window in config.windows]
    targets = grids[0].append(grids[1:]).rename("target_start_utc")
    context = FeatureContext(CausalHistory(
        observations, targets, targets, config.effective["task"]["publication_delay_hours"],
    ))
    values, quality, metadata = [], [], []
    for specification in config.effective["features"]["families"]:
        family = get_family(specification["id"], specification["version"], specification["configuration"])
        result = family.build(context)
        values.append(result.values)
        for column in family.columns:
            quality.extend([
                result.valid[column].rename(f"{column}__valid"),
                result.reasons[column].rename(f"{column}__reason"),
                result.available_at[column].rename(f"{column}__available_at"),
            ])
        metadata.append({
            "id": family.family_id, "version": family.version, "configuration": family.configuration,
            "columns": list(family.columns), "required_fields": list(family.required_fields),
            "lookback_hours": family.lookback_hours, "uses_measurements": family.uses_measurements,
        })
    features = pd.concat(values, axis=1)
    if features.columns.duplicated().any():
        raise ValueError("Feature recipe produced duplicate columns")
    quality_frame = pd.concat(quality, axis=1)
    source = observations.set_index("period_start_utc")
    examples = pd.DataFrame(index=targets)
    examples["target_end_utc"] = targets + pd.Timedelta(hours=1)
    examples["forecast_issue_time_utc"] = targets
    examples["split"] = [window.name for window, grid in zip(config.windows, grids) for _ in grid]
    examples["target_source_present"] = targets.isin(source.index)
    examples["target_consumption"] = source["consumption"].reindex(targets)
    examples["target_available"] = examples["target_consumption"].notna()
    examples["features_available"] = features.notna().all(axis=1)
    examples["missing_feature_count"] = features.isna().sum(axis=1)
    examples["complete_example"] = examples["target_available"] & examples["features_available"]
    examples["training_example_eligible"] = examples["split"].eq("train") & examples["complete_example"]
    examples["scoring_reference_available"] = examples["target_available"]
    counts = {}
    for window in config.windows:
        members = examples["split"].eq(window.name)
        subset = examples.loc[members]
        counts[window.name] = {
            "intended_examples": len(subset), "target_source_present": int(subset["target_source_present"].sum()),
            "target_available": int(subset["target_available"].sum()),
            "features_available": int(subset["features_available"].sum()),
            "complete_examples": int(subset["complete_example"].sum()),
            "training_examples_eligible": int(subset["training_example_eligible"].sum()),
            "unavailable_by_feature": {column: int(features.loc[members, column].isna().sum()) for column in features},
        }
    run_id = f"features_{uuid4().hex}"
    report = {
        "schema_version": FEATURE_SCHEMA_VERSION, "run_id": run_id,
        "status": "completed_with_warnings", "training_ready": False, "research_eligibility": "pending_review",
        "experiment_id": config.effective["experiment_id"], "configuration_fingerprint": config.fingerprint,
        "effective_configuration": config.effective, "feature_columns": features.columns.tolist(),
        "families": metadata, "counts_by_split": counts,
        "checks": {"input_integrity": "passed", "audit_window_agreement": "passed",
                   "feature_schema": "passed", "declared_issue_time_availability": "passed",
                   "source_publication_times": "not_run", "point_in_time_reconstruction": "not_run",
                   "learned_preprocessing": "not_run", "model_training": "not_run"},
        "source_limitations": audit_report["source_limitations"],
        "audit_review_required": audit_report["review_required"],
        "availability_semantics": "Sequential forecasts at target start; only source period end plus configured delay <= issue time",
        "warnings": [
            "Availability delay is an explicit retrospective assumption, not verified provider publication time",
            "Historical revisions cannot be removed by timestamp checks on a latest-value snapshot",
            "Target availability is retrospective reference availability, not a feature or knowledge at issue time",
            "No rows dropped, no targets filled, no learned preprocessing fitted, and no models trained",
        ],
    }
    destination = output_dir / run_id
    destination.mkdir(parents=True, exist_ok=False)
    try:
        features.to_csv(destination / "features.csv", index=True)
        examples.to_csv(destination / "examples.csv", index=True)
        quality_frame.to_csv(destination / "feature_quality.csv", index=True)
        Archive(destination / "feature_report.json").write(report)
        if artifact_checksum(config.path) != config.sha256:
            raise ValueError("Configuration changed during feature generation")
        _verify_inputs(config)
        package_root = Path(__file__).parents[1]
        sources = [Path(__file__), package_root / "pipeline" / "research_config.py",
                   package_root / "pipeline" / "research_audit.py",
                   *(package_root / "transformation" / "features").rglob("*.py")]
        Archive(destination / "manifest.json").write({
            "schema_version": FEATURE_SCHEMA_VERSION, "run_id": run_id,
            "executed_at_utc": datetime.now(timezone.utc).isoformat(),
            "configuration_path": str(config.path), "configuration_sha256": config.sha256,
            "configuration_fingerprint": config.fingerprint,
            "cleaned_manifest_sha256": config.effective["inputs"]["cleaned"]["manifest_sha256"],
            "audit_manifest_sha256": config.effective["inputs"]["audit"]["manifest_sha256"],
            "cleaning_output_sha256": cleaning_manifest["output_sha256"],
            "feature_columns": features.columns.tolist(),
            "output_sha256": {name: artifact_checksum(destination / name) for name in FEATURE_ARTIFACTS},
            "implementation_sha256": {str(path.relative_to(package_root)): artifact_checksum(path) for path in sorted(sources)},
            "software_versions": {"python": sys.version, "pandas": pd.__version__},
        })
        validate_feature_run(destination)
    except BaseException:
        shutil.rmtree(destination)
        raise
    return FeatureRun(destination, report)