"""Verify read-only audited research execution and CLI failure semantics."""

from datetime import date, datetime, timezone

import pandas as pd
import pytest

from oslo_energy.ingestion.archive import Archive
from oslo_energy.pipeline.cleaning import artifact_checksum, run_cleaning
from oslo_energy.pipeline.normalization import normalize_archive
from oslo_energy.pipeline.research_audit import run_research_audit, validate_research_audit
from oslo_energy.pipeline import run_research_audit as audit_cli
from oslo_energy.transformation.research_audit import ResearchWindow


@pytest.fixture
def cleaned(tmp_path):
    path = tmp_path / "raw.json"
    start = pd.Timestamp("2025-01-01", tz="Europe/Oslo")
    Archive(path).write({
        "StartPointUTC": int(start.timestamp() * 1000),
        "EndPointUTC": int((start + pd.Timedelta(hours=23)).timestamp() * 1000),
        "PeriodTickMs": 3600000, "Production": [1] * 24,
        "Consumption": [0, None, 1, 100] + [2] * 20,
    })
    normalized = normalize_archive(
        path, fetched_at=datetime(2026, 1, 1, tzinfo=timezone.utc), output_dir=tmp_path / "normalized",
    )
    return run_cleaning(normalized.output_dir, output_dir=tmp_path / "cleaned").output_dir


def test_audit_preserves_cleaned_artifacts_and_records_frozen_lineage(tmp_path, cleaned):
    before = {path.name: artifact_checksum(path) for path in cleaned.iterdir()}
    options = {"windows": [ResearchWindow("train", date(2025, 1, 1), date(2025, 1, 1))],
               "output_dir": tmp_path / "audits"}
    first = run_research_audit(cleaned, **options)
    second = run_research_audit(cleaned, **options)
    assert before == {path.name: artifact_checksum(path) for path in cleaned.iterdir()}
    assert first.output_dir != second.output_dir
    assert first.report["windows"] == second.report["windows"]
    assert first.report["research_eligibility"] == "pending_review"
    assert first.report["training_ready"] is False
    assert first.report["checks"]["research_window_coverage"] == "passed"
    assert first.report["checks"]["consumption_completeness"] == "findings"
    assert first.report["checks"]["source_accuracy"] == "not_run"
    assert first.report["checks"]["threshold_screening"] == "not_run"
    assert first.report["original_requested_range_coverage"] == "not_run"
    manifest = validate_research_audit(first.output_dir)
    assert manifest["configuration_fingerprint"] == validate_research_audit(second.output_dir)["configuration_fingerprint"]
    assert manifest["cleaning_manifest_sha256"] == before["manifest.json"]
    assert manifest["audit_report_sha256"] == artifact_checksum(first.output_dir / "audit_report.json")
    assert len(first.report["windows"][0]["zero_consumption_intervals"]) == 1


def test_missing_edges_are_research_findings_not_original_request_claims(tmp_path, cleaned):
    result = run_research_audit(
        cleaned, windows=[ResearchWindow("test", date(2024, 12, 31), date(2025, 1, 2))],
        output_dir=tmp_path / "audits",
    )
    window = result.report["windows"][0]
    assert window["expected_hour_count"] == 72
    assert window["absent_hour_count"] == 48
    assert result.report["checks"]["research_window_coverage"] == "findings"
    assert result.report["original_requested_range_coverage"] == "not_run"


def test_source_intervals_are_context_not_automatic_exclusions(tmp_path, cleaned):
    result = run_research_audit(
        cleaned, windows=[ResearchWindow("test", date(2025, 1, 1), date(2025, 12, 31))],
        output_dir=tmp_path / "audits",
    )
    context = result.report["windows"][0]["known_source_context"]
    assert context[0]["metric"] == "production"
    assert context[0]["overlap_hour_count"] == 552
    assert context[1]["metric"] == "consumption"
    assert "not proof every hour" in context[1]["detail"]
    assert result.report["windows"][0]["present_hour_count"] == 24


@pytest.mark.parametrize("windows", [[],
    [ResearchWindow("train", date(2025, 1, 1), date(2025, 1, 2)),
     ResearchWindow("test", date(2025, 1, 2), date(2025, 1, 3))],
    [ResearchWindow("test", date(2025, 1, 1), date(2025, 1, 1)),
     ResearchWindow("test", date(2025, 1, 2), date(2025, 1, 2))],
])
def test_invalid_window_sets_fail_before_output(tmp_path, cleaned, windows):
    with pytest.raises(ValueError):
        run_research_audit(cleaned, windows=windows, output_dir=tmp_path / "audits")
    assert not (tmp_path / "audits").exists()


def test_tampered_cleaning_artifact_is_rejected(tmp_path, cleaned):
    (cleaned / "observations.csv").write_text("changed\n")
    with pytest.raises(ValueError, match="checksum"):
        run_research_audit(cleaned, windows=[ResearchWindow("train", date(2025, 1, 1), date(2025, 1, 1))])


def test_outputs_cannot_be_nested_in_input(cleaned):
    with pytest.raises(ValueError, match="outside"):
        run_research_audit(cleaned, windows=[ResearchWindow("train", date(2025, 1, 1), date(2025, 1, 1))], output_dir=cleaned / "audits")


def test_failure_writing_completion_manifest_cleans_only_new_run(tmp_path, cleaned, monkeypatch):
    original = Archive.write

    def fail_manifest(self, payload):
        if self.file_path.name == "manifest.json":
            raise OSError("injected audit failure")
        original(self, payload)

    monkeypatch.setattr(Archive, "write", fail_manifest)
    with pytest.raises(OSError, match="injected"):
        run_research_audit(cleaned, windows=[ResearchWindow("train", date(2025, 1, 1), date(2025, 1, 1))], output_dir=tmp_path / "audits")
    assert list((tmp_path / "audits").iterdir()) == []
    assert (cleaned / "manifest.json").exists()


def test_cli_reports_pending_decision_and_nonzero_on_failure(tmp_path, cleaned, capsys):
    assert audit_cli.main(["--input-dir", str(cleaned), "--window", "train:2025-01-01:2025-01-01",
                           "--output-dir", str(tmp_path / "audits")]) == 0
    assert "eligibility pending review" in capsys.readouterr().out
    assert audit_cli.main(["--input-dir", str(tmp_path / "missing"), "--window", "train:2025-01-01:2025-01-01"]) == 1
    assert "Research audit failed" in capsys.readouterr().err


@pytest.mark.parametrize("window", ["train:bad:2025-01-01", "../test:2025-01-01:2025-01-01", "test:2025-02-01:2025-01-01"])
def test_cli_invalid_window_is_argument_error(cleaned, window):
    with pytest.raises(SystemExit) as exc:
        audit_cli.main(["--input-dir", str(cleaned), "--window", window])
    assert exc.value.code == 2


def test_incomplete_and_tampered_audits_are_rejected(tmp_path, cleaned):
    with pytest.raises(FileNotFoundError):
        validate_research_audit(tmp_path)
    result = run_research_audit(
        cleaned, windows=[ResearchWindow("train", date(2025, 1, 1), date(2025, 1, 1))],
        output_dir=tmp_path / "audits",
    )
    (result.output_dir / "audit_report.json").write_text("{}\n")
    with pytest.raises(ValueError, match="checksum"):
        validate_research_audit(result.output_dir)


def test_input_changes_during_audit_prevent_publication(tmp_path, cleaned, monkeypatch):
    from oslo_energy.pipeline import research_audit

    original_profile = research_audit.profile_window

    def changed_input(*args, **kwargs):
        report = original_profile(*args, **kwargs)
        with (cleaned / "observations.csv").open("a") as artifact:
            artifact.write("\n")
        return report

    monkeypatch.setattr(research_audit, "profile_window", changed_input)
    with pytest.raises(ValueError, match="checksum"):
        run_research_audit(cleaned, windows=[ResearchWindow("train", date(2025, 1, 1), date(2025, 1, 1))], output_dir=tmp_path / "audits")
    assert list((tmp_path / "audits").iterdir()) == []


def test_audit_does_not_infer_missing_future_hours_were_provider_gaps(tmp_path, cleaned):
    result = run_research_audit(
        cleaned, windows=[ResearchWindow("future", date(2026, 1, 1), date(2026, 1, 1))],
        output_dir=tmp_path / "audits", absolute_change_threshold=1000,
    )
    assert result.report["windows"][0]["hours_not_completed_at_capture"] == 23
    assert result.report["windows"][0]["absent_hour_count"] == 24
    assert result.report["checks"]["threshold_screening"] == "completed"