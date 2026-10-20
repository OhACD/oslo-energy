"""Blueprint schemas, safe creation, and a generated notebook's clean-kernel execution."""

import nbformat
import pytest

from test_features import configured

from oslo_energy.ingestion.archive import Archive
from oslo_energy.pipeline.blueprint import generate_experiment_blueprint, pending_blueprint_fields
from oslo_energy.pipeline.cleaning import artifact_checksum
from oslo_energy.pipeline.create_experiment import main
from oslo_energy.pipeline.experiment import ExperimentExecution
from oslo_energy.pipeline.research_config import load_research_config
from oslo_energy.benchmarking.config import load_benchmark_config
from oslo_energy.pipeline.run_experiment import execute_experiment, validate_notebook_execution


def test_default_blueprint_is_complete_source_but_explicitly_unconfigured(tmp_path):
    result = generate_experiment_blueprint("003_draft_v1", experiments_dir=tmp_path)
    assert {path.name for path in result.output_dir.iterdir()} == {"README.md", "feature_config.json", "benchmark.json", "experiment.ipynb"}
    assert len(result.pending_fields) == 8
    assert pending_blueprint_fields(result.output_dir) == result.pending_fields
    assert "No data was fetched" in (result.output_dir / "README.md").read_text()
    notebook = nbformat.read(result.output_dir / "experiment.ipynb", as_version=4)
    nbformat.validate(notebook)
    for cell in notebook.cells:
        assert cell.metadata.language in ("markdown", "python")
        if cell.cell_type == "code":
            compile(cell.source, "blueprint", "exec")
            assert cell.execution_count is None and not cell.outputs
    with pytest.raises(ValueError):
        ExperimentExecution.begin(result.output_dir)
    assert not (result.output_dir / "runs").exists()


def test_generator_pins_only_supplied_cleaned_run_and_windows(tmp_path, configured):
    original = load_research_config(configured)
    checksum = artifact_checksum(original.cleaned_dir / "manifest.json")
    result = generate_experiment_blueprint(
        "003_ready_v1", experiments_dir=tmp_path / "studies", cleaned_run=original.cleaned_dir, windows=original.windows,
    )
    assert result.pending_fields == ()
    config = load_research_config(result.output_dir / "feature_config.json")
    assert config.cleaned_dir == original.cleaned_dir
    assert config.windows == original.windows
    assert config.effective["inputs"]["cleaned"]["manifest_sha256"] == checksum
    assert "audit" not in config.effective["inputs"]
    benchmark = load_benchmark_config(result.output_dir / "benchmark.json", allow_generated_features=True)
    assert benchmark.feature_dir is None
    assert benchmark.effective["evaluation_split"] == "validation"
    assert artifact_checksum(original.cleaned_dir / "manifest.json") == checksum


def test_existing_experiment_is_never_overwritten(tmp_path):
    result = generate_experiment_blueprint("003_existing_v1", experiments_dir=tmp_path)
    before = {path.name: path.read_bytes() for path in result.output_dir.iterdir()}
    with pytest.raises(FileExistsError):
        generate_experiment_blueprint("003_existing_v1", experiments_dir=tmp_path)
    assert before == {path.name: path.read_bytes() for path in result.output_dir.iterdir()}


@pytest.mark.parametrize("settings", [
    {"experiment_id": "../escape"}, {"experiment_id": "Bad Name"},
    {"model_ids": ["arbitrary.module"]}, {"model_ids": ["linear_regression"]},
    {"plot_ids": ["unknown"]}, {"lags": [0]}, {"lags": [1, 24]}, {"publication_delay_hours": -1},
])
def test_invalid_blueprint_options_fail_before_creation(tmp_path, settings):
    experiment_id = settings.pop("experiment_id", "003_invalid_v1")
    with pytest.raises(ValueError):
        generate_experiment_blueprint(experiment_id, experiments_dir=tmp_path, **settings)
    assert not (tmp_path / experiment_id).exists()


def test_failed_generation_cleans_only_its_new_directory(tmp_path, monkeypatch):
    original = Archive.write
    unrelated = tmp_path / "unrelated.txt"
    unrelated.write_text("keep\n")

    def fail_benchmark(self, value):
        if self.file_path.name == "benchmark.json":
            raise OSError("injected blueprint write failure")
        original(self, value)

    monkeypatch.setattr(Archive, "write", fail_benchmark)
    with pytest.raises(OSError, match="injected"):
        generate_experiment_blueprint("003_failed_v1", experiments_dir=tmp_path)
    assert not (tmp_path / "003_failed_v1").exists()
    assert unrelated.read_text() == "keep\n"


def test_cli_reports_pending_settings_and_rejects_overwrite(tmp_path, capsys):
    args = ["003_cli_v1", "--experiments-dir", str(tmp_path)]
    assert main(args) == 0
    assert "Complete these explicit settings" in capsys.readouterr().out
    assert main(args) == 1
    assert "Blueprint generation failed" in capsys.readouterr().err


def test_cli_can_populate_verified_inputs_and_all_three_windows(tmp_path, configured, capsys):
    original = load_research_config(configured)
    arguments = ["003_cli_ready_v1", "--experiments-dir", str(tmp_path / "studies"),
                 "--cleaned-run", str(original.cleaned_dir)]
    for window in original.windows:
        arguments.extend(["--window", f"{window.name}:{window.from_date}:{window.to_date}"])
    assert main(arguments) == 0
    assert "populated; review all settings" in capsys.readouterr().out
    assert pending_blueprint_fields(tmp_path / "studies" / "003_cli_ready_v1") == ()


def test_generated_notebook_runs_with_synthetic_inputs_without_historical_experiment_dependency(tmp_path, configured):
    original = load_research_config(configured)
    result = generate_experiment_blueprint(
        "003_synthetic_v1", experiments_dir=tmp_path / "studies", cleaned_run=original.cleaned_dir, windows=original.windows,
    )
    before = {path: artifact_checksum(path) for path in result.output_dir.iterdir()}
    run_dir = execute_experiment(result.output_dir)
    validate_notebook_execution(run_dir)
    report = Archive(run_dir / "experiment_report.json").read()
    assert report["checks"]["final_test_scored"] is False
    assert before == {path: artifact_checksum(path) for path in before}
    benchmark = Archive(run_dir / report["stage_directories"]["benchmark"] / "benchmark_report.json").read()
    assert benchmark["evaluation_split"] == "validation"
    assert set(benchmark["models"]) == {"seasonal_naive", "persistence", "linear_regression"}
    assert benchmark["configuration"]["feature_run"]["path"] == str(run_dir / report["stage_directories"]["features"])