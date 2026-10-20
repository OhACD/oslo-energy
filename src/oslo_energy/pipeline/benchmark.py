"""Run fixed-grid model comparisons without implicitly exposing the final test."""

from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import shutil
import sys
from uuid import uuid4

import pandas as pd

from oslo_energy.benchmarking.config import load_benchmark_config
from oslo_energy.benchmarking.data import load_benchmark_dataset
from oslo_energy.benchmarking.evaluation import evaluate_predictions
from oslo_energy.benchmarking.plotting import PLOT_REGISTRY, PlotContext, get_plot, render_plots
from oslo_energy.ingestion.archive import Archive
from oslo_energy.models import PredictionResult, get_model
from oslo_energy.pipeline.cleaning import artifact_checksum
from oslo_energy.pipeline.features import validate_feature_run


BENCHMARK_ARTIFACTS = {"predictions.csv", "metrics.json", "benchmark_report.json"}


@dataclass(frozen=True)
class BenchmarkRun:
    output_dir: Path
    report: dict


def _software_versions() -> dict:
    result = {"python": sys.version, "pandas": pd.__version__}
    for name in ("scikit-learn", "scipy", "numpy", "matplotlib"):
        try:
            result[name] = version(name)
        except PackageNotFoundError:
            result[name] = None
    return result


def validate_benchmark_run(output_dir: Path) -> dict:
    manifest = Archive(output_dir / "manifest.json").read()
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "1":
        raise ValueError("Incompatible benchmark manifest")
    hashes = manifest.get("output_sha256")
    if not isinstance(hashes, dict) or not BENCHMARK_ARTIFACTS.issubset(hashes):
        raise ValueError("Incomplete benchmark manifest")
    report = Archive(output_dir / "benchmark_report.json").read()
    diagrams = report.get("visualizations", [])
    allowed_diagrams = {"benchmark_comparison.png", *(f"plots/{plot.filename}" for plot in PLOT_REGISTRY.values())}
    if not isinstance(diagrams, list) or any(not isinstance(name, str) for name in diagrams) or len(set(diagrams)) != len(diagrams) or set(diagrams) - allowed_diagrams:
        raise ValueError("Unknown benchmark visualizations")
    states = {f"models/{name}/state.json" for name, outcome in report["models"].items() if outcome["status"] == "completed"}
    if set(hashes) != BENCHMARK_ARTIFACTS | states | set(diagrams):
        raise ValueError("Benchmark manifest and model-state declarations disagree")
    for name, checksum in hashes.items():
        path = output_dir / name
        if output_dir.resolve() not in path.resolve().parents or artifact_checksum(path) != checksum:
            raise ValueError(f"Benchmark artifact integrity failure: {name}")
    if (
        report.get("schema_version") != "1" or report.get("run_id") != manifest.get("run_id")
        or report.get("training_ready") is not False or report.get("upstream_research_eligibility") != "pending_review"
        or report.get("configuration_fingerprint") != manifest.get("configuration_fingerprint")
        or report.get("status") not in ("completed_with_warnings", "completed_with_model_failures")
    ):
        raise ValueError("Inconsistent benchmark completion metadata")
    return manifest


def recompute_benchmark_metrics(output_dir: Path) -> dict:
    """Recalculate scoring from saved predictions, statuses, and fixed references."""
    validate_benchmark_run(output_dir)
    report = Archive(output_dir / "benchmark_report.json").read()
    table = pd.read_csv(output_dir / "predictions.csv", float_precision="round_trip")
    results, reference = {}, None
    for name in report["models"]:
        subset = table.loc[table["model_id"] == name].copy()
        subset.index = pd.to_datetime(subset.pop("target_start_utc"), utc=True)
        current = subset["reference_consumption"].astype("float64")
        if reference is None:
            reference = current
        else:
            pd.testing.assert_series_equal(reference, current, check_exact=True)
        results[name] = PredictionResult(subset["prediction"].astype("float64"), subset["status"])
    return evaluate_predictions(reference, results, baselines=report["configuration"]["baselines"])


def run_benchmark(
    config_path: Path, *, output_dir: Path | None = None, allow_test_evaluation: bool = False,
) -> BenchmarkRun:
    config = load_benchmark_config(config_path)
    if config.feature_dir is None:
        raise ValueError("Standalone benchmarking requires a pinned feature run")
    experiment_runs = config.path.parent / "runs"
    output_dir = (output_dir or experiment_runs).resolve()
    if output_dir == config.feature_dir or config.feature_dir in output_dir.parents:
        raise ValueError("Benchmark output must be outside feature input directories")
    if output_dir == config.path.parent or (
        config.path.parent in output_dir.parents and output_dir != experiment_runs and experiment_runs not in output_dir.parents
    ):
        raise ValueError("Benchmark output inside an experiment must use its runs directory; otherwise stay outside configuration directories")
    settings = config.effective
    dataset = load_benchmark_dataset(
        config.feature_dir, expected_manifest_sha256=settings["feature_run"]["manifest_sha256"],
        evaluation_split=settings["evaluation_split"], allow_test_evaluation=allow_test_evaluation,
    )
    forecast, reference = dataset.forecast_batch(), dataset.reference()
    predictions, outcomes, models = {}, {}, {}
    for item in settings["models"]:
        model = get_model(item["id"], item["version"], item["configuration"])
        try:
            if model.requires_fit:
                model.fit(dataset.training_batch(model.required_columns(tuple(dataset.features.columns))))
            predictions[model.model_id] = model.predict(forecast)
            models[model.model_id] = model
            outcomes[model.model_id] = {"status": "completed", "fit_provenance": model.fit_provenance,
                                        "configuration": model.configuration, "version": model.version}
        except Exception as exc:
            predictions[model.model_id] = PredictionResult(
                pd.Series(float("nan"), index=forecast.features.index, dtype="float64"),
                pd.Series("failed", index=forecast.features.index),
            )
            outcomes[model.model_id] = {"status": "failed", "error_type": type(exc).__name__, "error": str(exc),
                                        "configuration": model.configuration, "version": model.version}
    metrics = evaluate_predictions(reference, predictions, baselines=settings["baselines"])
    tables = []
    for name, result in predictions.items():
        table = pd.DataFrame({
            "model_id": name, "forecast_issue_time_utc": forecast.issue_times,
            "prediction": result.values, "status": result.status, "reference_consumption": reference,
            "reference_available": reference.notna(),
        }, index=forecast.features.index)
        table.index.name = "target_start_utc"
        tables.append(table)
    run_id = f"benchmark_{uuid4().hex}"
    report = {
        "schema_version": "1", "run_id": run_id,
        "status": "completed_with_model_failures" if any(item["status"] == "failed" for item in outcomes.values()) else "completed_with_warnings",
        "training_ready": False, "upstream_research_eligibility": "pending_review",
        "research_scope": "Explicitly permitted provisional retrospective API-reference comparison only",
        "configuration": settings, "configuration_fingerprint": config.fingerprint,
        "evaluation_split": dataset.evaluation_split, "fit_cutoff_utc": dataset.fit_cutoff.isoformat(),
        "feature_columns": dataset.features.columns.tolist(), "models": outcomes,
        "visualizations": [f"plots/{get_plot(plot_id).filename}" for plot_id in settings["plots"]],
        "plot_definitions": [{"id": plot_id, "version": get_plot(plot_id).version,
                      "filename": get_plot(plot_id).filename} for plot_id in settings["plots"]],
        "intended_forecasts_per_model": len(reference),
        "source_limitations": dataset.feature_report["source_limitations"],
        "upstream_review_required": dataset.feature_report["audit_review_required"],
        "warnings": [
            "No operational point-in-time accuracy or independently verified reference is established",
            "Publication delay and revised historical inputs remain source limitations",
            "No imputation, test-driven tuning, validation refit, or model selection is performed",
            "The candidate test period previously underwent descriptive inspection; disclose any influence on design",
        ],
        "test_evaluation_explicitly_allowed": bool(allow_test_evaluation and dataset.evaluation_split == "test"),
    }
    destination = output_dir / run_id
    destination.mkdir(parents=True, exist_ok=False)
    try:
        predictions_table = pd.concat(tables)
        predictions_table.to_csv(destination / "predictions.csv", index=True)
        Archive(destination / "metrics.json").write(metrics)
        Archive(destination / "benchmark_report.json").write(report)
        for name, model in models.items():
            model.save(destination / "models" / name / "state.json")
        local = forecast.features.index.tz_convert("Europe/Oslo")
        rendered = render_plots(settings["plots"], PlotContext(
            metrics, predictions_table,
            f"Norway consumption | {dataset.evaluation_split}: {local[0].date()} to {local[-1].date()}",
        ), destination)
        if rendered != report["visualizations"]:
            raise ValueError("Rendered plots disagree with declared artifacts")
        if artifact_checksum(config.path) != config.sha256:
            raise ValueError("Benchmark configuration changed during execution")
        if artifact_checksum(config.feature_dir / "manifest.json") != dataset.feature_manifest_sha256:
            raise ValueError("Feature manifest changed during benchmark execution")
        validate_feature_run(config.feature_dir)
        package_root = Path(__file__).parents[1]
        sources = [Path(__file__), *(package_root / "models").rglob("*.py"), *(package_root / "benchmarking").rglob("*.py")]
        artifacts = [path for path in destination.rglob("*") if path.is_file()]
        Archive(destination / "manifest.json").write({
            "schema_version": "1", "run_id": run_id, "executed_at_utc": datetime.now(timezone.utc).isoformat(),
            "configuration_sha256": config.sha256, "configuration_fingerprint": config.fingerprint,
            "feature_manifest_sha256": dataset.feature_manifest_sha256, "feature_dir": str(config.feature_dir),
            "output_sha256": {str(path.relative_to(destination)): artifact_checksum(path) for path in sorted(artifacts)},
            "implementation_sha256": {str(path.relative_to(package_root)): artifact_checksum(path) for path in sorted(sources)},
            "software_versions": _software_versions(),
        })
        validate_benchmark_run(destination)
    except BaseException:
        shutil.rmtree(destination)
        raise
    return BenchmarkRun(destination, report)