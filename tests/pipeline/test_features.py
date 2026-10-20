"""Exercise causal feature artifacts and keep targets out of the downstream X API."""

from datetime import date, datetime, timezone

import pandas as pd
import pytest

from oslo_energy.ingestion.archive import Archive
from oslo_energy.pipeline.cleaning import artifact_checksum, run_cleaning
from oslo_energy.pipeline.features import load_feature_matrix, run_features, validate_feature_run
from oslo_energy.pipeline.normalization import normalize_archive
from oslo_energy.pipeline.research_audit import run_research_audit
from oslo_energy.pipeline.research_config import load_research_config
from oslo_energy.pipeline import run_features as feature_cli
from oslo_energy.pipeline import run_research_audit as audit_cli
from oslo_energy.transformation.research_audit import ResearchWindow


def config_data(cleaned, audit, experiment):
    return {
        "schema_version": "1", "experiment_id": "first_features_v1", "status": "draft",
        "purpose": "provisional_feature_generation",
        "inputs": {name: {"path": str(directory.relative_to(experiment.parent)),
                          "manifest_sha256": artifact_checksum(directory / "manifest.json")}
                   for name, directory in (("cleaned", cleaned), ("audit", audit))},
        "task": {"target": "consumption", "horizon_hours": 1, "timezone": "Europe/Oslo",
                 "issue_time": "target_start", "publication_delay_hours": 0,
                 "availability_assumption": "completed_hour_plus_configured_delay_retrospective"},
        "windows": [{"name": name, "from_date": f"2025-01-0{number}", "to_date": f"2025-01-0{number}"}
                    for number, name in enumerate(("train", "validation", "test"), start=1)],
        "features": {"recipe_id": "lag_calendar", "recipe_version": "1", "families": [
            {"id": "consumption_lags", "version": "1", "configuration": {"lags": [1, 24, 168]}},
            {"id": "calendar", "version": "1", "configuration": {}},
        ]},
        "treatment": {"suspicious_values": "retain", "missing_inputs": "preserve", "missing_targets": "preserve", "drop_rows": False},
    }


@pytest.fixture
def configured(tmp_path):
    start = pd.Timestamp("2024-12-23T23:00:00Z")
    count = 24 * 11
    values = [float(number + 1) for number in range(count)]
    target_position = int((pd.Timestamp("2025-01-02T11:00:00Z") - start) / pd.Timedelta(hours=1))
    values[target_position] = None
    raw = tmp_path / "raw.json"
    Archive(raw).write({
        "StartPointUTC": int(start.timestamp() * 1000),
        "EndPointUTC": int((start + pd.Timedelta(hours=count - 1)).timestamp() * 1000),
        "PeriodTickMs": 3600000, "Consumption": values, "Production": [None] * count,
    })
    normalized = normalize_archive(raw, fetched_at=datetime(2026, 1, 1, tzinfo=timezone.utc), output_dir=tmp_path / "normalized")
    cleaned = run_cleaning(normalized.output_dir, output_dir=tmp_path / "cleaned").output_dir
    windows = [ResearchWindow(name, date(2025, 1, number), date(2025, 1, number))
               for number, name in enumerate(("train", "validation", "test"), start=1)]
    audit = run_research_audit(cleaned, windows=windows, output_dir=tmp_path / "audits").output_dir
    experiment = tmp_path / "experiment"
    data = config_data(cleaned, audit, experiment)
    for reference in data["inputs"].values():
        reference["path"] = "../" + reference["path"]
    path = experiment / "config.json"
    Archive(path).write(data)
    return path


def test_feature_run_retains_grid_and_separates_target_from_model_inputs(tmp_path, configured):
    config = load_research_config(configured)
    hashes = {path: artifact_checksum(path) for path in config.cleaned_dir.iterdir()}
    result = run_features(configured, output_dir=tmp_path / "features")
    manifest = validate_feature_run(result.output_dir)
    matrix = load_feature_matrix(result.output_dir)
    examples = pd.read_csv(result.output_dir / "examples.csv")
    assert matrix.shape == (72, 7)
    assert all("consumption_lag_" in name or name.startswith("oslo_") for name in matrix)
    assert "target_consumption" not in matrix
    assert "target_available" not in matrix
    assert "split" not in matrix
    assert len(load_feature_matrix(result.output_dir, split="test")) == 24
    assert len(examples) == 72
    assert result.report["counts_by_split"]["train"]["complete_examples"] == 24
    assert examples.loc[examples["split"] != "train", "training_example_eligible"].eq(False).all()
    assert not examples["target_available"].all()
    assert examples.loc[~examples["target_available"], "features_available"].all()
    assert result.report["training_ready"] is False
    assert result.report["research_eligibility"] == "pending_review"
    assert manifest["configuration_sha256"] == artifact_checksum(configured)
    assert hashes == {path: artifact_checksum(path) for path in config.cleaned_dir.iterdir()}


def test_repeated_runs_are_semantically_deterministic(tmp_path, configured):
    first = run_features(configured, output_dir=tmp_path / "features")
    second = run_features(configured, output_dir=tmp_path / "features")
    assert first.output_dir != second.output_dir
    pd.testing.assert_frame_equal(load_feature_matrix(first.output_dir), load_feature_matrix(second.output_dir))
    assert first.report["counts_by_split"] == second.report["counts_by_split"]


def test_shared_config_runs_audit_and_features_without_changing_window_flags(tmp_path, configured, capsys):
    assert audit_cli.main(["--config", str(configured), "--output-dir", str(tmp_path / "other_audit")]) == 0
    assert feature_cli.main(["--config", str(configured), "--output-dir", str(tmp_path / "features")]) == 0
    assert "No model trained" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        audit_cli.main(["--config", str(configured), "--window", "test:2025-01-01:2025-01-01"])


@pytest.mark.parametrize("change", ["windows", "cleaned_hash", "audit_hash"])
def test_mismatched_references_fail_before_writes(tmp_path, configured, change):
    raw = Archive(configured).read()
    if change == "windows":
        raw["windows"][0]["from_date"] = "2024-12-31"
    else:
        name = "cleaned" if change == "cleaned_hash" else "audit"
        raw["inputs"][name]["manifest_sha256"] = "0" * 64
    configured.unlink()
    Archive(configured).write(raw)
    with pytest.raises(ValueError):
        run_features(configured, output_dir=tmp_path / "features")
    assert not (tmp_path / "features").exists()


def test_tampered_feature_artifact_cannot_feed_matrix_loader(tmp_path, configured):
    result = run_features(configured, output_dir=tmp_path / "features")
    (result.output_dir / "features.csv").write_text("target_consumption\n99\n")
    with pytest.raises(ValueError, match="checksum"):
        load_feature_matrix(result.output_dir)


def test_failed_write_removes_only_new_feature_run(tmp_path, configured, monkeypatch):
    original = Archive.write

    def fail_manifest(self, data):
        if self.file_path.name == "manifest.json":
            raise OSError("injected feature failure")
        original(self, data)

    monkeypatch.setattr(Archive, "write", fail_manifest)
    with pytest.raises(OSError, match="injected"):
        run_features(configured, output_dir=tmp_path / "features")
    assert list((tmp_path / "features").iterdir()) == []


def test_outputs_cannot_overwrite_inputs_or_configuration(configured):
    for output in (configured.parent, load_research_config(configured).cleaned_dir):
        with pytest.raises(ValueError, match="outside"):
            run_features(configured, output_dir=output)


def test_feature_stage_may_write_only_inside_designated_experiment_runs(configured):
    before = artifact_checksum(configured)
    result = run_features(configured, output_dir=configured.parent / "runs" / "feature_stage")
    validate_feature_run(result.output_dir)
    assert result.output_dir.parent == configured.parent / "runs" / "feature_stage"
    assert artifact_checksum(configured) == before
    with pytest.raises(ValueError, match="runs/"):
        run_features(configured, output_dir=configured.parent / "not_artifacts")


def test_manifest_column_list_cannot_turn_a_label_into_a_feature(tmp_path, configured):
    result = run_features(configured, output_dir=tmp_path / "features")
    report_path = result.output_dir / "feature_report.json"
    manifest_path = result.output_dir / "manifest.json"
    report = Archive(report_path).read()
    manifest = Archive(manifest_path).read()
    report["feature_columns"] = ["target_consumption"]
    manifest["feature_columns"] = ["target_consumption"]
    report_path.unlink()
    Archive(report_path).write(report)
    manifest["output_sha256"]["feature_report.json"] = artifact_checksum(report_path)
    manifest_path.unlink()
    Archive(manifest_path).write(manifest)
    with pytest.raises(ValueError, match="metadata"):
        load_feature_matrix(result.output_dir)


def test_configuration_mutation_during_generation_prevents_publication(tmp_path, configured, monkeypatch):
    from oslo_energy.pipeline import features

    original = features.load_cleaned_observations

    def mutate_config(*args):
        observations = original(*args)
        with configured.open("a") as stream:
            stream.write("\n")
        return observations

    monkeypatch.setattr(features, "load_cleaned_observations", mutate_config)
    with pytest.raises(ValueError, match="Configuration changed"):
        run_features(configured, output_dir=tmp_path / "features")
    assert list((tmp_path / "features").iterdir()) == []


def test_explicit_delay_changes_input_eligibility_not_target_labels(tmp_path, configured):
    raw = Archive(configured).read()
    raw["task"]["publication_delay_hours"] = 1
    configured.unlink()
    Archive(configured).write(raw)
    result = run_features(configured, output_dir=tmp_path / "features")
    examples = pd.read_csv(result.output_dir / "examples.csv")
    assert examples["features_available"].eq(False).all()
    assert examples["target_available"].sum() == 71
    assert load_feature_matrix(result.output_dir)["consumption_lag_1h"].isna().all()