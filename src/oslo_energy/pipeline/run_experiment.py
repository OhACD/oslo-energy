"""Execute an experiment notebook from a fresh kernel using this Python interpreter."""

import argparse
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from typing import Sequence

from oslo_energy.ingestion.archive import Archive
from oslo_energy.pipeline.cleaning import artifact_checksum
from oslo_energy.pipeline.experiment import validate_experiment_run


def _execution_path(notebook, experiment_dir: Path) -> Path | None:
    paths = set()
    for cell in notebook.cells:
        for output in cell.get("outputs", []):
            data = output.get("data", {}).get("application/json", {})
            if isinstance(data, dict) and "experiment_run_dir" in data:
                paths.add(Path(data["experiment_run_dir"]).resolve())
    if not paths:
        return None
    if len(paths) != 1:
        raise ValueError("Notebook emitted conflicting execution identities")
    path = paths.pop()
    if path.parent != experiment_dir / "runs" or not path.name.startswith("experiment_"):
        raise ValueError("Notebook execution identity is outside the experiment runs directory")
    return path


def validate_notebook_execution(run_dir: Path) -> dict:
    validate_experiment_run(run_dir)
    report = Archive(run_dir / "notebook_execution.json").read()
    if (
        report.get("status") != "completed"
        or report.get("executed_notebook_sha256") != artifact_checksum(run_dir / "experiment.executed.ipynb")
        or report.get("experiment_manifest_sha256") != artifact_checksum(run_dir / "manifest.json")
        or report.get("source_notebook_sha256") != artifact_checksum(run_dir / "sources" / "experiment.ipynb")
    ):
        raise ValueError("Notebook execution is incomplete or its artifacts were modified")
    return report


def execute_experiment(experiment_dir: Path) -> Path:
    """Run the source notebook top-to-bottom; never overwrite source outputs or settings."""
    import nbformat
    import nbclient
    from nbclient import NotebookClient
    from jupyter_client import KernelManager
    from jupyter_client.kernelspec import KernelSpecManager

    experiment_dir = experiment_dir.resolve()
    source_path = experiment_dir / "experiment.ipynb"
    source_hash = artifact_checksum(source_path)
    notebook = nbformat.read(source_path, as_version=4)
    nbformat.validate(notebook)
    for cell in notebook.cells:
        if cell.cell_type == "code":
            cell.outputs = []
            cell.execution_count = None
    error = None
    with TemporaryDirectory(prefix="oslo-notebook-kernel-") as temporary:
        specification = Path(temporary) / "oslo-experiment" / "kernel.json"
        Archive(specification).write({
            "argv": [sys.executable, "-m", "ipykernel_launcher", "-f", "{connection_file}"],
            "display_name": "Oslo experiment interpreter", "language": "python",
        })
        manager = KernelManager(kernel_name="oslo-experiment", kernel_spec_manager=KernelSpecManager(kernel_dirs=[temporary]))
        client = NotebookClient(notebook, km=manager, timeout=600, allow_errors=False,
                                resources={"metadata": {"path": str(experiment_dir)}})
        try:
            client.execute(cleanup_kc=True)
        except Exception as exc:
            error = exc
        finally:
            if manager.has_kernel:
                manager.shutdown_kernel(now=True)
    run_dir = _execution_path(notebook, experiment_dir)
    if run_dir is None:
        if error:
            raise error
        raise ValueError("Notebook did not emit an execution identity")
    with (run_dir / "experiment.executed.ipynb").open("x", encoding="utf-8") as artifact:
        nbformat.write(notebook, artifact)
    if artifact_checksum(source_path) != source_hash:
        raise ValueError("Source notebook changed during execution")
    Archive(run_dir / "notebook_execution.json").write({
        "schema_version": "1", "status": "failed" if error else "completed",
        "source_notebook_sha256": source_hash,
        "executed_notebook_sha256": artifact_checksum(run_dir / "experiment.executed.ipynb"),
        "experiment_manifest_sha256": artifact_checksum(run_dir / "manifest.json") if not error else None,
        "interpreter": sys.executable, "nbformat_version": nbformat.__version__, "nbclient_version": nbclient.__version__,
        "error": str(error) if error else None,
    })
    if error:
        raise error
    validate_notebook_execution(run_dir)
    return run_dir


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a validation experiment notebook in a fresh project-interpreter kernel.")
    parser.add_argument("--experiment-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        run_dir = execute_experiment(args.experiment_dir)
    except Exception as exc:
        print(f"Notebook experiment failed: {exc}", file=sys.stderr)
        return 1
    print(f"Completed notebook experiment: {run_dir}")
    print(f"Executed notebook: {run_dir / 'experiment.executed.ipynb'}")
    print("Validation only; training_ready=false; final test remains unscored.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())