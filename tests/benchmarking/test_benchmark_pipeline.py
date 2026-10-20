"""Synthetic artifact integration, temporal fit guards, and reproducible benchmark scoring."""

from copy import deepcopy

import pandas as pd
import pytest

from oslo_energy.benchmarking.config import load_benchmark_config
from oslo_energy.benchmarking.data import load_benchmark_dataset
from oslo_energy.ingestion.archive import Archive
from oslo_energy.models import load_model
from oslo_energy.pipeline.benchmark import recompute_benchmark_metrics, run_benchmark, validate_benchmark_run
from oslo_energy.pipeline.cleaning import artifact_checksum
from oslo_energy.pipeline import run_benchmark as benchmark_cli


@pytest.fixture
def benchmark_config(tmp_path):
    root = tmp_path / "features"
    root.mkdir()
    index = pd.date_range("2025-01-01", periods=72, freq="h", tz="Europe/Oslo").tz_convert("UTC").rename("target_start_utc")
    features = pd.DataFrame({"consumption_lag_1h": [float(number) for number in range(72)],
                             "consumption_lag_168h": [float((number % 5) ** 2) for number in range(72)]}, index=index)
    features.loc[index[30], "consumption_lag_1h"] = float("nan")
    labels = 2 * pd.Series(range(72), index=index) + 3 * features["consumption_lag_168h"] + 5
    labels.loc[index[29]] = float("nan")
    examples = pd.DataFrame({"target_end_utc": index + pd.Timedelta(hours=1), "forecast_issue_time_utc": index,
                             "split": [name for name in ("train", "validation", "test") for _ in range(24)],
                             "target_source_present": True, "target_consumption": labels}, index=index)
    examples["target_available"] = labels.notna()
    examples["features_available"] = features.notna().all(axis=1)
    examples["missing_feature_count"] = features.isna().sum(axis=1)
    examples["complete_example"] = examples["target_available"] & examples["features_available"]
    examples["training_example_eligible"] = examples["split"].eq("train") & examples["complete_example"]
    examples["scoring_reference_available"] = examples["target_available"]
    features.to_csv(root / "features.csv")
    examples.to_csv(root / "examples.csv")
    quality = pd.DataFrame(index=index)
    for hours in (1, 168):
        column = f"consumption_lag_{hours}h"
        quality[f"{column}__valid"] = features[column].notna()
        quality[f"{column}__reason"] = "available"
        quality.loc[features[column].isna(), f"{column}__reason"] = "missing_measurement"
        quality[f"{column}__available_at"] = index - pd.Timedelta(hours=hours - 1)
    quality.to_csv(root / "feature_quality.csv")
    report = {
        "schema_version": "1", "run_id": "synthetic_features", "training_ready": False,
        "research_eligibility": "pending_review", "configuration_fingerprint": "synthetic",
        "feature_columns": list(features.columns), "source_limitations": {"reference": "synthetic test only"},
        "audit_review_required": ["Synthetic fixture is not a scientific result"],
        "effective_configuration": {
            "task": {"target": "consumption", "horizon_hours": 1, "issue_time": "target_start", "publication_delay_hours": 0,
                     "timezone": "Europe/Oslo", "availability_assumption": "completed_hour_plus_configured_delay_retrospective"},
            "windows": [{"name": name, "from_date": f"2025-01-0{number}", "to_date": f"2025-01-0{number}"}
                        for number, name in enumerate(("train", "validation", "test"), start=1)],
            "features": {"families": [{"id": "consumption_lags", "version": "1", "configuration": {"lags": [1, 168]}}]},
        },
    }
    Archive(root / "feature_report.json").write(report)
    Archive(root / "manifest.json").write({
        "schema_version": "1", "run_id": "synthetic_features", "configuration_fingerprint": "synthetic",
        "feature_columns": list(features.columns),
        "output_sha256": {name: artifact_checksum(root / name) for name in ("features.csv", "examples.csv", "feature_quality.csv", "feature_report.json")},
    })
    path = tmp_path / "experiment" / "benchmark.json"
    Archive(path).write({
        "schema_version": "1", "benchmark_id": "synthetic_comparison",
        "feature_run": {"path": "../features", "manifest_sha256": artifact_checksum(root / "manifest.json")},
        "models": [{"id": name, "version": "1", "configuration": {}} for name in ("seasonal_naive", "persistence", "linear_regression")],
        "baselines": ["seasonal_naive", "persistence"], "provisional_research": True,
    })
    return path


def rewrite(path, data):
    path.unlink()
    Archive(path).write(data)


def rehash_feature_artifact(config_path, filename):
    config = load_benchmark_config(config_path)
    path = config.feature_dir / "manifest.json"
    manifest = Archive(path).read()
    manifest["output_sha256"][filename] = artifact_checksum(config.feature_dir / filename)
    rewrite(path, manifest)
    raw = Archive(config_path).read()
    raw["feature_run"]["manifest_sha256"] = artifact_checksum(path)
    rewrite(config_path, raw)


def test_synthetic_benchmark_defaults_to_validation_and_metrics_recompute(tmp_path, benchmark_config):
    config = load_benchmark_config(benchmark_config)
    before = {path.name: artifact_checksum(path) for path in config.feature_dir.iterdir()}
    run = run_benchmark(benchmark_config, output_dir=tmp_path / "benchmarks")
    manifest = validate_benchmark_run(run.output_dir)
    metrics = Archive(run.output_dir / "metrics.json").read()
    assert recompute_benchmark_metrics(run.output_dir) == metrics
    assert run.report["evaluation_split"] == "validation"
    assert run.report["training_ready"] is False
    assert run.report["models"]["linear_regression"]["fit_provenance"]["fitted_rows"] == 24
    assert metrics["individual"]["seasonal_naive"]["predicted"] == 24
    assert metrics["individual"]["linear_regression"]["predicted"] == 23
    assert metrics["individual"]["linear_regression"]["scored"] == 22
    assert metrics["individual"]["linear_regression"]["errors"]["mae"] < 1e-10
    assert metrics["paired_against_baselines"]["linear_regression"]["seasonal_naive"]["model"]["count"] == 22
    assert manifest["feature_manifest_sha256"] == config.effective["feature_run"]["manifest_sha256"]
    assert run.report["visualizations"] == ["plots/benchmark_comparison.png"]
    assert "plots/benchmark_comparison.png" in manifest["output_sha256"]
    assert (run.output_dir / "plots" / "benchmark_comparison.png").read_bytes().startswith(b"\x89PNG")
    assert before == {path.name: artifact_checksum(path) for path in config.feature_dir.iterdir()}
    data = load_benchmark_dataset(config.feature_dir, expected_manifest_sha256=config.effective["feature_run"]["manifest_sha256"])
    restored = load_model(run.output_dir / "models" / "linear_regression" / "state.json")
    assert restored.predict(data.forecast_batch()).values.notna().sum() == 23


def test_final_test_requires_explicit_opt_in_before_output(tmp_path, benchmark_config):
    raw = Archive(benchmark_config).read()
    raw["evaluation_split"] = "test"
    rewrite(benchmark_config, raw)
    with pytest.raises(ValueError, match="explicit opt-in"):
        run_benchmark(benchmark_config, output_dir=tmp_path / "benchmarks")
    assert not (tmp_path / "benchmarks").exists()
    result = run_benchmark(benchmark_config, output_dir=tmp_path / "benchmarks", allow_test_evaluation=True)
    assert result.report["test_evaluation_explicitly_allowed"] is True


def test_validation_execution_does_not_parse_test_labels(tmp_path, benchmark_config):
    config = load_benchmark_config(benchmark_config)
    path = config.feature_dir / "examples.csv"
    examples = pd.read_csv(path, dtype=str, keep_default_na=False)
    examples.loc[examples["split"] == "test", "target_consumption"] = "not_read_for_validation"
    examples.to_csv(path, index=False)
    rehash_feature_artifact(benchmark_config, "examples.csv")
    run = run_benchmark(benchmark_config, output_dir=tmp_path / "benchmarks")
    assert run.report["status"] == "completed_with_warnings"


def test_heldout_feature_and_label_changes_do_not_change_fitted_state(tmp_path, benchmark_config):
    first = run_benchmark(benchmark_config, output_dir=tmp_path / "benchmarks")
    before = Archive(first.output_dir / "models" / "linear_regression" / "state.json").read()["state"]
    config = load_benchmark_config(benchmark_config)
    path = config.feature_dir / "features.csv"
    frame = pd.read_csv(path)
    frame.loc[24:, "consumption_lag_168h"] *= 10000
    frame.to_csv(path, index=False)
    rehash_feature_artifact(benchmark_config, "features.csv")
    path = config.feature_dir / "examples.csv"
    examples = pd.read_csv(path)
    mask = examples["split"].eq("validation") & examples["target_available"]
    examples.loc[mask, "target_consumption"] *= 1000
    examples.to_csv(path, index=False)
    rehash_feature_artifact(benchmark_config, "examples.csv")
    second = run_benchmark(benchmark_config, output_dir=tmp_path / "benchmarks")
    assert Archive(second.output_dir / "models" / "linear_regression" / "state.json").read()["state"] == before


@pytest.mark.parametrize("broken", ["split", "eligibility", "time", "label_mask"])
def test_example_metadata_cannot_bypass_temporal_or_availability_gates(tmp_path, benchmark_config, broken):
    config = load_benchmark_config(benchmark_config)
    path = config.feature_dir / "examples.csv"
    frame = pd.read_csv(path)
    column, value = {"split": ("split", "train"), "eligibility": ("training_example_eligible", True),
                     "time": ("forecast_issue_time_utc", "2025-01-01T00:00:00Z"), "label_mask": ("target_available", False)}[broken]
    frame.loc[25, column] = value
    frame.to_csv(path, index=False)
    rehash_feature_artifact(benchmark_config, "examples.csv")
    with pytest.raises(ValueError):
        run_benchmark(benchmark_config, output_dir=tmp_path / "benchmarks")
    assert not (tmp_path / "benchmarks").exists()


def test_failed_model_is_counted_and_cli_returns_nonzero(tmp_path, benchmark_config, monkeypatch):
    from oslo_energy.models.adapters.linear_regression import LinearRegressionAdapter

    def broken_fit(self, batch):
        raise RuntimeError("injected estimator failure")

    monkeypatch.setattr(LinearRegressionAdapter, "_fit", broken_fit)
    run = run_benchmark(benchmark_config, output_dir=tmp_path / "benchmarks")
    assert run.report["status"] == "completed_with_model_failures"
    metrics = Archive(run.output_dir / "metrics.json").read()
    assert metrics["individual"]["linear_regression"]["failed"] == 24
    assert metrics["individual"]["linear_regression"]["errors"]["mae"] is None
    validate_benchmark_run(run.output_dir)
    assert benchmark_cli.main(["--config", str(benchmark_config), "--output-dir", str(tmp_path / "benchmarks")]) == 1


def test_failed_completion_write_removes_only_new_output(tmp_path, benchmark_config, monkeypatch):
    original = Archive.write

    def fail_manifest(self, payload):
        if self.file_path.name == "manifest.json":
            raise OSError("injected write failure")
        original(self, payload)

    monkeypatch.setattr(Archive, "write", fail_manifest)
    with pytest.raises(OSError, match="injected"):
        run_benchmark(benchmark_config, output_dir=tmp_path / "benchmarks")
    assert list((tmp_path / "benchmarks").iterdir()) == []


def test_tampered_results_and_config_hash_mismatches_fail(tmp_path, benchmark_config):
    run = run_benchmark(benchmark_config, output_dir=tmp_path / "benchmarks")
    (run.output_dir / "metrics.json").write_text("{}\n")
    with pytest.raises(ValueError, match="integrity"):
        validate_benchmark_run(run.output_dir)
    raw = Archive(benchmark_config).read()
    raw["feature_run"]["manifest_sha256"] = "0" * 64
    rewrite(benchmark_config, raw)
    with pytest.raises(ValueError, match="pinned"):
        run_benchmark(benchmark_config, output_dir=tmp_path / "benchmarks")


@pytest.mark.parametrize("broken", ["permission", "model", "fallback", "duplicate", "baseline", "unknown"])
def test_invalid_benchmark_configuration_fails(benchmark_config, broken):
    raw = Archive(benchmark_config).read()
    if broken == "permission":
        raw["provisional_research"] = False
    elif broken == "model":
        raw["models"][0]["id"] = "arbitrary.module"
    elif broken == "fallback":
        raw["models"][0]["configuration"] = {"fallback": True}
    elif broken == "duplicate":
        raw["models"].append(deepcopy(raw["models"][0]))
    elif broken == "baseline":
        raw["baselines"] = ["linear_regression"]
    else:
        raw["unknown"] = True
    rewrite(benchmark_config, raw)
    with pytest.raises(ValueError):
        load_benchmark_config(benchmark_config)


def test_saved_future_feature_availability_is_rejected_before_fit(tmp_path, benchmark_config):
    config = load_benchmark_config(benchmark_config)
    path = config.feature_dir / "feature_quality.csv"
    quality = pd.read_csv(path)
    quality.loc[25, "consumption_lag_1h__available_at"] = "2025-01-10T00:00:00Z"
    quality.to_csv(path, index=False)
    rehash_feature_artifact(benchmark_config, "feature_quality.csv")
    with pytest.raises(ValueError, match="unavailable at issue"):
        run_benchmark(benchmark_config, output_dir=tmp_path / "benchmarks")
    assert not (tmp_path / "benchmarks").exists()


def test_mutated_benchmark_configuration_prevents_completion(tmp_path, benchmark_config, monkeypatch):
    from oslo_energy.models.adapters.naive import Persistence

    original = Persistence._predict

    def mutate(self, features):
        with benchmark_config.open("a") as stream:
            stream.write("\n")
        return original(self, features)

    monkeypatch.setattr(Persistence, "_predict", mutate)
    with pytest.raises(ValueError, match="configuration changed"):
        run_benchmark(benchmark_config, output_dir=tmp_path / "benchmarks")
    assert list((tmp_path / "benchmarks").iterdir()) == []


def test_successful_cli_and_output_isolation(tmp_path, benchmark_config, capsys):
    assert benchmark_cli.main(["--config", str(benchmark_config), "--output-dir", str(tmp_path / "benchmarks")]) == 0
    assert "provisional retrospective results" in capsys.readouterr().out
    config = load_benchmark_config(benchmark_config)
    for destination in (config.feature_dir, benchmark_config.parent):
        with pytest.raises(ValueError, match="outside"):
            run_benchmark(benchmark_config, output_dir=destination)


def test_modified_diagram_fails_integrity_check(tmp_path, benchmark_config):
    run = run_benchmark(benchmark_config, output_dir=tmp_path / "benchmarks")
    (run.output_dir / "plots" / "benchmark_comparison.png").write_bytes(b"changed")
    with pytest.raises(ValueError, match="integrity"):
        validate_benchmark_run(run.output_dir)


def test_legacy_run_without_visualization_remains_verifiable(tmp_path, benchmark_config):
    run = run_benchmark(benchmark_config, output_dir=tmp_path / "benchmarks")
    report_path, manifest_path = run.output_dir / "benchmark_report.json", run.output_dir / "manifest.json"
    report = Archive(report_path).read()
    report.pop("visualizations")
    rewrite(report_path, report)
    manifest = Archive(manifest_path).read()
    manifest["output_sha256"].pop("plots/benchmark_comparison.png")
    manifest["output_sha256"]["benchmark_report.json"] = artifact_checksum(report_path)
    rewrite(manifest_path, manifest)
    (run.output_dir / "plots" / "benchmark_comparison.png").unlink()
    validate_benchmark_run(run.output_dir)


def test_default_outputs_live_inside_experiment_runs(benchmark_config):
    before = artifact_checksum(benchmark_config)
    run = run_benchmark(benchmark_config)
    assert run.output_dir.parent == benchmark_config.parent / "runs"
    assert (run.output_dir / "plots" / "benchmark_comparison.png").is_file()
    assert artifact_checksum(benchmark_config) == before


def test_config_selects_plots_without_changing_scoring(tmp_path, benchmark_config):
    raw = Archive(benchmark_config).read()
    raw["plots"] = []
    rewrite(benchmark_config, raw)
    run = run_benchmark(benchmark_config, output_dir=tmp_path / "benchmarks")
    assert run.report["visualizations"] == []
    assert not (run.output_dir / "plots").exists()
    assert recompute_benchmark_metrics(run.output_dir) == Archive(run.output_dir / "metrics.json").read()
    for selection in (["arbitrary.module"], ["comparison", "comparison"]):
        raw["plots"] = selection
        rewrite(benchmark_config, raw)
        with pytest.raises(ValueError):
            load_benchmark_config(benchmark_config)