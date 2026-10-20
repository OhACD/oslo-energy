"""Run the actual notebook against tiny explicit synthetic settings in a fresh kernel."""

from pathlib import Path
import shutil

import nbformat
import pytest

from test_experiment_controller import experiment_dir
from test_features import configured

from oslo_energy.ingestion.archive import Archive
from oslo_energy.pipeline.cleaning import artifact_checksum
from oslo_energy.pipeline.run_experiment import execute_experiment, validate_notebook_execution


def test_notebook_runs_top_to_bottom_without_hidden_state(experiment_dir):
    source = Path(__file__).parents[2] / "experiments" / "002_baselines_linear_validation_v1" / "experiment.ipynb"
    shutil.copyfile(source, experiment_dir / "experiment.ipynb")
    before = artifact_checksum(experiment_dir / "experiment.ipynb")
    run_dir = execute_experiment(experiment_dir)
    execution = validate_notebook_execution(run_dir)
    assert execution["status"] == "completed"
    assert artifact_checksum(experiment_dir / "experiment.ipynb") == before
    notebook = nbformat.read(run_dir / "experiment.executed.ipynb", as_version=4)
    assert all(cell.execution_count is not None for cell in notebook.cells if cell.cell_type == "code")
    report = Archive(run_dir / "experiment_report.json").read()
    assert report["checks"]["final_test_scored"] is False
    assert (run_dir / report["stage_directories"]["benchmark"] / "plots" / "benchmark_comparison.png").is_file()
    with (run_dir / "experiment.executed.ipynb").open("a") as artifact:
        artifact.write("\n")
    with pytest.raises(ValueError, match="modified"):
        validate_notebook_execution(run_dir)


def test_failed_notebook_never_publishes_successful_completion(experiment_dir):
    source = Path(__file__).parents[2] / "experiments" / "002_baselines_linear_validation_v1" / "experiment.ipynb"
    notebook = nbformat.read(source, as_version=4)
    position = next(position for position, cell in enumerate(notebook.cells)
                    if cell.cell_type == "code" and cell.source.startswith("execution ="))
    notebook.cells.insert(position + 1, nbformat.v4.new_code_cell("raise RuntimeError('injected notebook failure')"))
    nbformat.write(notebook, experiment_dir / "experiment.ipynb")
    with pytest.raises(Exception, match="injected notebook failure"):
        execute_experiment(experiment_dir)
    runs = list((experiment_dir / "runs").glob("experiment_*"))
    assert len(runs) == 1
    assert Archive(runs[0] / "notebook_execution.json").read()["status"] == "failed"
    assert not (runs[0] / "manifest.json").exists()
    assert (runs[0] / "experiment.executed.ipynb").is_file()