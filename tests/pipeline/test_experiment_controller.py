"""Reuse the feature fixture to verify notebook-facing end-to-end orchestration."""

import pytest

from test_features import configured

from oslo_energy.ingestion.archive import Archive
from oslo_energy.pipeline.benchmark import recompute_benchmark_metrics
from oslo_energy.pipeline.cleaning import artifact_checksum
from oslo_energy.pipeline.experiment import ExperimentExecution, validate_experiment_run
from oslo_energy.pipeline.features import run_features


@pytest.fixture
def experiment_dir(tmp_path, configured):
    original_feature_config = Archive(configured).read()
    Archive(configured.parent / "feature_config.json").write(original_feature_config)
    feature_run = run_features(configured, output_dir=tmp_path / "reference_features")
    Archive(configured.parent / "benchmark.json").write({
        "schema_version": "1", "benchmark_id": "synthetic_notebook_comparison",
        "feature_run": {"path": str(feature_run.output_dir), "manifest_sha256": artifact_checksum(feature_run.output_dir / "manifest.json")},
        "models": [{"id": name, "version": "1", "configuration": {}}
                   for name in ("seasonal_naive", "persistence", "linear_regression")],
        "baselines": ["seasonal_naive", "persistence"], "provisional_research": True,
        "evaluation_split": "validation", "plots": ["comparison"],
    })
    return configured.parent


def test_controller_runs_all_stages_without_editing_source_configs(experiment_dir):
    paths = [experiment_dir / name for name in ("feature_config.json", "benchmark.json")]
    before = {path: artifact_checksum(path) for path in paths}
    execution = ExperimentExecution.begin(experiment_dir)
    with pytest.raises(ValueError, match="audit"):
        execution.features()
    execution.audit()
    execution.features()
    execution.benchmark()
    report = execution.complete()
    validate_experiment_run(execution.run_dir)
    assert report["checks"]["all_model_reloads"] == "passed"
    assert report["checks"]["final_test_scored"] is False
    assert before == {path: artifact_checksum(path) for path in paths}
    resolved = Archive(execution.resolved_configs["benchmark"]).read()
    assert resolved["feature_run"]["path"] == str(execution.feature_run.output_dir)
    assert execution.benchmark_run.report["configuration"]["feature_run"] == resolved["feature_run"]
    assert (execution.benchmark_run.output_dir / "plots" / "benchmark_comparison.png").is_file()
    assert recompute_benchmark_metrics(execution.benchmark_run.output_dir) == Archive(execution.benchmark_run.output_dir / "metrics.json").read()
    with pytest.raises(ValueError):
        execution.audit()


def test_controller_rejects_test_scope_and_source_mutation(experiment_dir):
    execution = ExperimentExecution.begin(experiment_dir)
    with (experiment_dir / "feature_config.json").open("a") as source:
        source.write("\n")
    with pytest.raises(ValueError, match="source changed"):
        execution.audit()
    assert not (execution.run_dir / "manifest.json").exists()
    path = experiment_dir / "benchmark.json"
    raw = Archive(path).read()
    raw["evaluation_split"] = "test"
    path.unlink()
    Archive(path).write(raw)
    with pytest.raises(ValueError, match="validation only"):
        ExperimentExecution.begin(experiment_dir)


def test_interactive_output_saves_do_not_hide_notebook_code_edits(experiment_dir):
    import nbformat

    path = experiment_dir / "experiment.ipynb"
    notebook = nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell("value = 1")])
    nbformat.write(notebook, path)
    execution = ExperimentExecution.begin(experiment_dir)
    notebook.cells[0].execution_count = 1
    notebook.cells[0].outputs = [nbformat.v4.new_output("stream", name="stdout", text="runtime output\n")]
    nbformat.write(notebook, path)
    execution.audit()
    notebook.cells[0].source = "value = 2"
    nbformat.write(notebook, path)
    with pytest.raises(ValueError, match="notebook content changed"):
        execution.features()