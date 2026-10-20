"""Exercise cleaning adapters and offline execution using normalized fixtures."""

from datetime import date, datetime, timezone

import pandas as pd
import pytest

from oslo_energy.ingestion.archive import Archive
from oslo_energy.pipeline import run_cleaning as cleaning_cli
from oslo_energy.pipeline.cleaning import (
    artifact_checksum, load_cleaning_input, run_cleaning, validate_cleaning_run,
)
from oslo_energy.pipeline.normalization import normalize_archive
from oslo_energy.pipeline.planning import DateRange


def normalized_run(tmp_path, *, start="2025-01-01T00:00:00Z", count=4, fetched_at=None, requested=None):
    start = pd.Timestamp(start)
    path = tmp_path / "raw.json"
    Archive(path).write({
        "StartPointUTC": int(start.timestamp() * 1000),
        "EndPointUTC": int((start + pd.Timedelta(hours=count - 1)).timestamp() * 1000),
        "PeriodTickMs": 3600000, "Production": [None] + [2] * (count - 1),
        "Consumption": [4] * (count - 1) + [None],
    })
    return normalize_archive(
        path, fetched_at=fetched_at or datetime(2026, 1, 1, tzinfo=timezone.utc),
        output_dir=tmp_path / "normalized", requested_range=requested,
    )


def rewrite_report(directory, **updates):
    path = directory / "quality.json"
    report = Archive(path).read()
    report.update(updates)
    path.unlink()
    Archive(path).write(report)


def test_adapter_preserves_nulls_and_marks_unknown_endpoint_coverage(tmp_path):
    run = normalized_run(tmp_path)
    loaded = load_cleaning_input(run.output_dir)
    pd.testing.assert_frame_equal(
        loaded.context.observations[run.data.historical.columns], run.data.historical,
    )
    assert loaded.checks["requested_range_coverage"] == "not_run"
    assert loaded.checks["statistical_behavior"] == "not_run"
    assert len([record for record in loaded.context.findings if record.code == "missing_measurement"]) == 2


@pytest.mark.parametrize("local_date, hours", [(date(2025, 3, 30), 23), (date(2025, 10, 26), 25)])
def test_dst_days_have_no_false_absent_hours(tmp_path, local_date, hours):
    start = pd.Timestamp(local_date, tz="Europe/Oslo").tz_convert("UTC")
    run = normalized_run(tmp_path, start=start, count=hours, requested=DateRange(local_date, local_date))
    loaded = load_cleaning_input(run.output_dir)
    assert not any(record.code == "absent_hour" for record in loaded.context.findings)
    assert len(loaded.context.observations) == hours


def test_bounded_request_reports_absent_edges_without_inventing_rows(tmp_path):
    run = normalized_run(tmp_path, requested=DateRange(date(2025, 1, 1), date(2025, 1, 1)))
    loaded = load_cleaning_input(run.output_dir)
    findings = [record for record in loaded.context.findings if record.code == "absent_hour"]
    assert len(findings) == 20
    assert loaded.checks["internal_continuity"] == "passed"
    assert len(loaded.context.observations) == 4
    assert all(record.metric is None and record.action is None for record in findings)


def test_whole_run_null_counts_are_not_historical_null_counts(tmp_path):
    run = normalized_run(tmp_path, fetched_at=datetime(2025, 1, 1, 3, 30, tzinfo=timezone.utc))
    loaded = load_cleaning_input(run.output_dir)
    assert len(loaded.context.observations) == 3
    assert loaded.context.observations["consumption"].notna().all()
    assert loaded.checks["whole_run_missingness"] == "passed"
    assert loaded.context.normalization_report["consumption_missing_count"] == 1


@pytest.mark.parametrize("updates", [
    {"frequency": "daily"}, {"historical_count": 99}, {"training_ready": True},
    {"source_archive": ""}, {"timezone": "UTC"}, {"production_missing_rate": 0.5},
    {"request_coverage": {}}, {"fetched_at_utc": "2026-01-01"},
])
def test_contradictory_metadata_fails(tmp_path, updates):
    run = normalized_run(tmp_path)
    rewrite_report(run.output_dir, **updates)
    with pytest.raises(ValueError):
        load_cleaning_input(run.output_dir)


@pytest.mark.parametrize("column, value", [
    ("consumption", "garbage"), ("production", "NaN"), ("production", "inf"),
    ("production", "-1"), ("period_start_utc", "2025-01-01"),
    ("period_end_utc", "2025-01-01T03:00:00Z"), ("observation_date", "2024-12-31"),
    ("source_index", "0.5"), ("period_hours", "2"),
])
def test_invalid_observation_values_fail(tmp_path, column, value):
    run = normalized_run(tmp_path)
    path = run.output_dir / "historical_candidates.csv"
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    frame.loc[0, column] = value
    frame.to_csv(path, index=False)
    with pytest.raises(ValueError):
        load_cleaning_input(run.output_dir)


def test_duplicate_identity_fails(tmp_path):
    run = normalized_run(tmp_path)
    path = run.output_dir / "historical_candidates.csv"
    frame = pd.read_csv(path)
    frame.loc[1, "period_start_utc"] = frame.loc[0, "period_start_utc"]
    frame.loc[1, "period_end_utc"] = frame.loc[0, "period_end_utc"]
    frame.to_csv(path, index=False)
    with pytest.raises(ValueError, match="Duplicate"):
        load_cleaning_input(run.output_dir)


def test_optional_incomplete_context_and_future_rows_are_not_required(tmp_path):
    run = normalized_run(tmp_path, fetched_at=datetime(2025, 1, 1, 1, 30, tzinfo=timezone.utc))
    (run.output_dir / "incomplete.csv").unlink()
    (run.output_dir / "future_quarantine.csv").unlink()
    loaded = load_cleaning_input(run.output_dir)
    assert len(loaded.context.observations) == 1
    assert loaded.checks["incomplete_context"] == "not_run"
    assert loaded.checks["whole_run_missingness"] == "not_run"


def test_empty_historical_partition_is_valid(tmp_path):
    run = normalized_run(tmp_path, fetched_at=datetime(2025, 1, 1, 0, 30, tzinfo=timezone.utc))
    loaded = load_cleaning_input(run.output_dir)
    assert loaded.context.observations.empty


def test_interior_gap_is_diagnostic_without_endpoint_claim(tmp_path):
    run = normalized_run(tmp_path)
    path = run.output_dir / "historical_candidates.csv"
    frame = pd.read_csv(path).drop(index=1)
    frame.to_csv(path, index=False)
    rewrite_report(
        run.output_dir, historical_count=3, observation_count=3, raw_slot_count=3,
        production_missing_rate=1 / 3, consumption_missing_rate=1 / 3, temporal_gap_count=1,
    )
    loaded = load_cleaning_input(run.output_dir)
    assert loaded.checks["requested_range_coverage"] == "not_run"
    assert sum(record.code == "absent_hour" for record in loaded.context.findings) == 1


@pytest.mark.parametrize("filename", ["quality.json", "historical_candidates.csv"])
def test_missing_required_artifact_fails(tmp_path, filename):
    run = normalized_run(tmp_path)
    (run.output_dir / filename).unlink()
    with pytest.raises(FileNotFoundError):
        load_cleaning_input(run.output_dir)


def test_outputs_are_preserved_audited_and_repeatable(tmp_path):
    normalized = normalized_run(tmp_path)
    original_hashes = {path: artifact_checksum(path) for path in normalized.output_dir.iterdir()}
    first = run_cleaning(normalized.output_dir, output_dir=tmp_path / "cleaned")
    second = run_cleaning(normalized.output_dir, output_dir=tmp_path / "cleaned")
    manifest = validate_cleaning_run(first.output_dir)
    other = validate_cleaning_run(second.output_dir)
    assert first.output_dir != second.output_dir
    assert manifest["configuration_fingerprint"] == other["configuration_fingerprint"]
    assert manifest["output_sha256"]["observations.csv"] == other["output_sha256"]["observations.csv"]
    assert manifest["output_sha256"]["row_quality.csv"] == other["output_sha256"]["row_quality.csv"]
    assert "manifest.json" not in manifest["output_sha256"]
    assert manifest["source_archive"]["sha256"] == artifact_checksum(tmp_path / "raw.json")
    assert original_hashes == {path: artifact_checksum(path) for path in normalized.output_dir.iterdir()}
    observations = pd.read_csv(first.output_dir / "observations.csv")
    for metric in ("production", "consumption"):
        pd.testing.assert_series_equal(observations[metric], observations[f"{metric}_original"], check_names=False)
    assert first.report["training_ready"] is False
    assert first.report["status"] == "completed_with_warnings"
    assert first.report["counts"]["changed_measurements"] == 0
    assert first.report["counts"]["excluded_observations"] == 0
    assert first.report["counts"]["synthetic_observations"] == 0
    assert first.report["actions_by_code"] == {"observation_retained": 4}
    records = pd.read_csv(first.output_dir / "row_quality.csv")
    assert set(records["kind"]) == {"diagnostic", "policy"}
    assert len(records.loc[records["action"] == "retain"]) == 4


def test_missing_source_archive_is_reported_not_required(tmp_path):
    normalized = normalized_run(tmp_path)
    (tmp_path / "raw.json").unlink()
    result = run_cleaning(normalized.output_dir, output_dir=tmp_path / "cleaned")
    assert validate_cleaning_run(result.output_dir)["source_archive"]["status"] == "unavailable"
    assert "Referenced source archive checksum unavailable" in result.report["warnings"]


def test_checksum_verification_rejects_modified_artifacts(tmp_path):
    normalized = normalized_run(tmp_path)
    result = run_cleaning(normalized.output_dir, output_dir=tmp_path / "cleaned")
    (result.output_dir / "observations.csv").write_text("changed\n")
    with pytest.raises(ValueError, match="checksum mismatch"):
        validate_cleaning_run(result.output_dir)


def test_failed_write_does_not_publish_a_completed_run(tmp_path, monkeypatch):
    normalized = normalized_run(tmp_path)
    original_write = Archive.write

    def fail_manifest(self, payload):
        if self.file_path.name == "manifest.json":
            raise OSError("injected failure")
        original_write(self, payload)

    monkeypatch.setattr(Archive, "write", fail_manifest)
    with pytest.raises(OSError, match="injected"):
        run_cleaning(normalized.output_dir, output_dir=tmp_path / "cleaned")
    assert list((tmp_path / "cleaned").iterdir()) == []


def test_incomplete_output_is_not_a_completed_run(tmp_path):
    with pytest.raises(FileNotFoundError):
        validate_cleaning_run(tmp_path)


@pytest.mark.parametrize("relative", [".", "cleaned"])
def test_outputs_cannot_be_inside_input_directory(tmp_path, relative):
    normalized = normalized_run(tmp_path)
    with pytest.raises(ValueError, match="outside"):
        run_cleaning(normalized.output_dir, output_dir=normalized.output_dir / relative)


def test_cli_runs_offline_and_reports_training_restriction(tmp_path, capsys):
    normalized = normalized_run(tmp_path)
    assert cleaning_cli.main([
        "--input-dir", str(normalized.output_dir), "--output-dir", str(tmp_path / "cleaned"),
    ]) == 0
    output = capsys.readouterr().out
    assert "completed_with_warnings" in output
    assert "Training readiness blocked" in output


def test_cli_failure_returns_nonzero_without_outputs(tmp_path, capsys):
    assert cleaning_cli.main([
        "--input-dir", str(tmp_path / "absent"), "--output-dir", str(tmp_path / "cleaned"),
    ]) == 1
    assert "Cleaning failed" in capsys.readouterr().err
    assert not (tmp_path / "cleaned").exists()


def test_cli_unknown_policy_is_argument_error(tmp_path):
    with pytest.raises(SystemExit) as exc:
        cleaning_cli.main(["--input-dir", str(tmp_path), "--policy", "imputation"])
    assert exc.value.code == 2


def test_empty_historical_output_is_a_valid_cleaning_run(tmp_path):
    normalized = normalized_run(tmp_path, fetched_at=datetime(2025, 1, 1, 0, 30, tzinfo=timezone.utc))
    result = run_cleaning(normalized.output_dir, output_dir=tmp_path / "cleaned")
    assert pd.read_csv(result.output_dir / "observations.csv").empty
    assert result.report["counts"]["output_observations"] == 0
    validate_cleaning_run(result.output_dir)


def test_input_change_during_processing_prevents_publication(tmp_path, monkeypatch):
    from oslo_energy.pipeline import cleaning

    normalized = normalized_run(tmp_path)
    original_loader = cleaning.load_cleaning_input

    def changed_input(path):
        loaded = original_loader(path)
        with (path / "historical_candidates.csv").open("a") as artifact:
            artifact.write("\n")
        return loaded

    monkeypatch.setattr(cleaning, "load_cleaning_input", changed_input)
    with pytest.raises(ValueError, match="changed during"):
        run_cleaning(normalized.output_dir, output_dir=tmp_path / "cleaned")
    assert list((tmp_path / "cleaned").iterdir()) == []