"""Generate portable experiment sources, never research results or guessed inputs."""

from dataclasses import dataclass
import os
from pathlib import Path
import re
import shlex
import shutil
from typing import Sequence

from oslo_energy.benchmarking.plotting import get_plot
from oslo_energy.ingestion.archive import Archive
from oslo_energy.models import get_model
from oslo_energy.pipeline.cleaning import artifact_checksum, validate_cleaning_run
from oslo_energy.pipeline.research_config import load_research_config, validate_windows
from oslo_energy.benchmarking.config import load_benchmark_config
from oslo_energy.transformation.features import get_family
from oslo_energy.transformation.research_audit import ResearchWindow


@dataclass(frozen=True)
class ExperimentBlueprint:
    output_dir: Path
    pending_fields: tuple[str, ...]


def pending_blueprint_fields(experiment_dir: Path) -> tuple[str, ...]:
    """Locate explicit placeholders rather than accepting fake paths or checksums."""
    pending = []

    def inspect(value, location):
        if isinstance(value, dict):
            for key, child in value.items():
                inspect(child, f"{location}.{key}")
        elif isinstance(value, list):
            for position, child in enumerate(value):
                inspect(child, f"{location}[{position}]")
        elif isinstance(value, str) and value.startswith("FILL_ME_"):
            pending.append(location)

    for name in ("feature_config.json", "benchmark.json"):
        inspect(Archive(experiment_dir / name).read(), name)
    return tuple(pending)


def _notebook(experiment_id: str):
    import nbformat

    cells = []

    def markdown(source):
        cells.append(nbformat.v4.new_markdown_cell(source, metadata={"language": "markdown"}))

    def code(source):
        cells.append(nbformat.v4.new_code_cell(source, metadata={"language": "python"}))

    markdown(f"# Experiment {experiment_id}\n\nDraft blueprint. Complete the source JSON settings before running. "
             "The notebook calls tested package APIs; it never collects live data, fits on held-out data, or scores the final test. "
             "Restart the kernel and Run All for a new execution. Model/data changes require a new experiment version.")
    code(
        "from pathlib import Path\nimport pandas as pd\nfrom IPython.display import Image, JSON, display\n"
        "from oslo_energy.ingestion.archive import Archive\n"
        "from oslo_energy.pipeline.blueprint import pending_blueprint_fields\n"
        "from oslo_energy.pipeline.experiment import ExperimentExecution\n\n"
        f"expected_experiment_id = {experiment_id!r}\n"
        "working_dir = Path.cwd().resolve()\n"
        "candidates = (working_dir, working_dir / 'experiments' / expected_experiment_id)\n"
        "experiment_dir = next((path for path in candidates if (path / 'feature_config.json').is_file() "
        "and Archive(path / 'feature_config.json').read().get('experiment_id') == expected_experiment_id), None)\n"
        "if experiment_dir is None:\n"
        "    raise ValueError('Run from this experiment directory, or the repository root for a standard experiments/ layout')\n"
        "pending = pending_blueprint_fields(experiment_dir)\n"
        "if pending:\n    raise ValueError('Complete these source settings before execution: ' + ', '.join(pending))\n"
        "execution = ExperimentExecution.begin(experiment_dir)\n"
        "display(JSON({'experiment_run_dir': str(execution.run_dir)}))\n"
        "display(JSON(execution.feature_config.effective))\n"
        "display(JSON(execution.benchmark_config.effective))"
    )
    markdown("## Audit the Configured Research Windows\n\nInspect missingness and source limitations. "
             "An audit is not source certification; do not tune settings from held-out outcomes.")
    code(
        "audit = execution.audit()\n"
        "display(pd.DataFrame([{'split': item['name'], 'expected_hours': item['expected_hour_count'], "
        "'present_hours': item['present_hour_count'], 'missing_consumption': item['consumption_null_count']} "
        "for item in audit.report['windows']]))"
    )
    markdown("## Build Causal Features\n\nUse the configured families and issue-time availability rules. "
             "Missing inputs/targets remain missing; all intended timestamps are retained. The new audit is explicitly linked in a resolved config.")
    code(
        "features = execution.features()\n"
        "display(pd.DataFrame(features.report['counts_by_split']).T[['intended_examples', 'target_available', "
        "'features_available', 'complete_examples']])\n"
        "print('Resolved feature config:', execution.resolved_configs['features'])"
    )
    markdown("## Train on Training Data and Evaluate Validation\n\nLearned preprocessing and models fit only eligible training examples. "
             "The benchmark is explicitly bound to this execution's freshly generated features. No final-test evaluation or automatic tuning occurs.")
    code(
        "benchmark = execution.benchmark()\n"
        "metrics = Archive(benchmark.output_dir / 'metrics.json').read()\n"
        "display(pd.DataFrame([{'model': name, 'MAE': item['errors']['mae'], 'RMSE': item['errors']['rmse'], "
        "'predicted': item['predicted'], 'scored': item['scored'], 'intended': item['intended'], "
        "'prediction_coverage': item['prediction_coverage_of_intended']} for name, item in metrics['individual'].items()]).set_index('model'))\n"
        "print('Resolved benchmark config:', execution.resolved_configs['benchmark'])"
    )
    markdown("## Compare the Selected Models\n\nPlots consume saved evaluation evidence. Errors are in unverified provider units, "
             "not generic percentage accuracy. Coverage and paired sample counts remain visible.")
    code(
        "for relative_path in benchmark.report['visualizations']:\n"
        "    display(Image(filename=str(benchmark.output_dir / relative_path)))\n"
        "display(pd.DataFrame([{'model': model_id, 'baseline': baseline_id, 'paired_count': item['model']['count'], "
        "'MAE_skill': item['mae_skill']} for model_id, baselines in metrics['paired_against_baselines'].items() "
        "for baseline_id, item in baselines.items()]))"
    )
    markdown("## Verify and Publish This Execution\n\nVerify stage checksums, recompute metrics, and reload every model. "
             "The headless runner additionally saves an executed notebook and completion record. Source settings and earlier runs are never overwritten. "
             "Publication times, revisions, and source accuracy remain unresolved; training readiness is not granted.")
    code(
        "report = execution.complete()\n"
        "display(JSON(report['checks']))\n"
        "print('Completed execution:', execution.run_dir)\n"
        "print('Provisional validation only; training_ready=false; final test unscored.')"
    )
    notebook = nbformat.v4.new_notebook(cells=cells, metadata={
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python"}, "experiment_blueprint": {"schema_version": "1"},
    })
    nbformat.validate(notebook)
    return notebook


def generate_experiment_blueprint(
    experiment_id: str, *, experiments_dir: Path = Path("experiments"), cleaned_run: Path | None = None,
    windows: Sequence[ResearchWindow] | None = None, lags: list[int] | None = None,
    model_ids: Sequence[str] = ("seasonal_naive", "persistence", "linear_regression"),
    plot_ids: Sequence[str] = ("comparison",), publication_delay_hours: int = 0,
) -> ExperimentBlueprint:
    """Create an exclusive source directory; incomplete drafts fail closed on Run All."""
    import nbformat

    if not isinstance(experiment_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_]*", experiment_id):
        raise ValueError("Experiment ID must use lowercase letters, digits, and underscores, without path separators")
    if type(publication_delay_hours) is not int or publication_delay_hours < 0:
        raise ValueError("publication_delay_hours must be a nonnegative integer")
    target = experiments_dir.resolve() / experiment_id
    lag_family = get_family("consumption_lags", configuration={"lags": [1, 24, 168] if lags is None else lags})
    calendar = get_family("calendar")
    columns = (*lag_family.columns, *calendar.columns)
    if not model_ids or len(set(model_ids)) != len(model_ids):
        raise ValueError("Select distinct registered model IDs")
    models = [get_model(model_id) for model_id in model_ids]
    for model in models:
        if set(model.required_columns(columns)) - set(columns):
            raise ValueError(f"Feature recipe does not provide required inputs for {model.model_id}")
    baselines = [model.model_id for model in models if not model.requires_fit]
    if not baselines:
        raise ValueError("Select at least one baseline for paired benchmarking")
    if len(set(plot_ids)) != len(plot_ids):
        raise ValueError("Select distinct registered plot IDs")
    for plot_id in plot_ids:
        get_plot(plot_id)
    if windows is not None:
        ordered = validate_windows(windows, split_roles=True)
        split_settings = [{"name": item.name, "from_date": item.from_date.isoformat(), "to_date": item.to_date.isoformat()} for item in ordered]
    else:
        split_settings = [{"name": name, "from_date": f"FILL_ME_{name.upper()}_FROM_DATE",
                           "to_date": f"FILL_ME_{name.upper()}_TO_DATE"} for name in ("train", "validation", "test")]
    reference = {"path": "FILL_ME_CLEANED_RUN_PATH", "manifest_sha256": "FILL_ME_CLEANED_MANIFEST_SHA256"}
    if cleaned_run is not None:
        cleaned_run = cleaned_run.resolve()
        if target == cleaned_run or cleaned_run in target.parents:
            raise ValueError("Blueprint must not be created inside a cleaned input run")
        validate_cleaning_run(cleaned_run)
        report = Archive(cleaned_run / "cleaning_report.json").read()
        if report["normalization_report"]["frequency"] != "hourly":
            raise ValueError("Blueprint requires an hourly cleaned run")
        reference = {"path": Path(os.path.relpath(cleaned_run, target)).as_posix(),
                     "manifest_sha256": artifact_checksum(cleaned_run / "manifest.json")}
    feature_config = {
        "schema_version": "1", "experiment_id": experiment_id, "status": "draft",
        "purpose": "provisional_feature_generation", "inputs": {"cleaned": reference},
        "task": {"target": "consumption", "horizon_hours": 1, "timezone": "Europe/Oslo", "issue_time": "target_start",
                 "publication_delay_hours": publication_delay_hours,
                 "availability_assumption": "completed_hour_plus_configured_delay_retrospective"},
        "windows": split_settings,
        "features": {"recipe_id": "lag_calendar", "recipe_version": "1", "families": [
            {"id": family.family_id, "version": family.version, "configuration": family.configuration}
            for family in (lag_family, calendar)
        ]},
        "treatment": {"suspicious_values": "retain", "missing_inputs": "preserve", "missing_targets": "preserve", "drop_rows": False},
    }
    benchmark_config = {
        "schema_version": "1", "benchmark_id": experiment_id, "feature_run": {"source": "experiment_features"},
        "models": [{"id": model.model_id, "version": model.version, "configuration": model.configuration} for model in models],
        "baselines": baselines, "provisional_research": True, "evaluation_split": "validation", "plots": list(plot_ids),
    }
    notebook = _notebook(experiment_id)
    command_path = shlex.quote(str(target))
    readme = f"""# Experiment {experiment_id}

**Draft blueprint, not a frozen result.** Fill and review source settings before execution. No data was fetched, no model trained, and no results copied from another experiment.

## Complete the Settings

1. In `feature_config.json`, replace every `FILL_ME_` value: the cleaned run path, its exact manifest SHA-256, and the three inclusive Oslo-date windows. Input paths resolve relative to this directory. Never use a guessed or zero-filled checksum.
2. Review families and lag settings, model settings, baselines, plots, and publication delay. Generated defaults are current supported baseline/linear methods, not an MLP. Keep required baseline lag columns when editing recipes.
3. Source audit and feature hashes are not required: the controller generates a fresh audit and features, then records explicit pinned references in this execution's resolved configs. `feature_run.source: experiment_features` is supported only by the notebook controller; standalone benchmark CLI requires a real pinned feature run.
4. `provisional_research: true` declares the restricted retrospective scope, not independent source accuracy or publication-time certainty. Confirm that scope before running. Zero publication delay is an assumption; a positive delay can make lag-1 unavailable rather than silently shifting it.
5. Keep intended evaluation timestamps and observed labels unchanged. Do not fit preprocessing on validation/test data, fill targets, or tune using final-test outcomes. The controller is validation-only.

The config settings are authoritative. `experiment.ipynb` calls tested package APIs, not a second implementation of feature engineering or model training. Do not add hidden overrides in cells. Custom families/models/plots must implement and register their contracts before selecting them in JSON.

## Run

From the repository root, install the complete interface:

```bash
python -m pip install -e '.[dev,modeling,notebooks]'
```

Open `experiment.ipynb` with the project interpreter, restart the kernel, and Run All. For a custom experiments root, use the experiment directory as the notebook working directory. Or execute the same source notebook in a fresh kernel:

```bash
python -m oslo_energy.pipeline.run_experiment --experiment-dir {command_path}
```

Each execution creates `runs/experiment_<id>/` here, including config/notebook snapshots, resolved configs, audit, features, benchmark predictions, metrics, fitted states, and plots. The headless runner adds `experiment.executed.ipynb` and `notebook_execution.json`. No prior run or source config is overwritten; retain generated runs separately from Git. The source notebook has no saved execution outputs.

## Record the Research Decision

Document the hypothesis, input identity, task/availability assumptions, split rationale, preprocessing, model settings, tuning budget, observed limitations, and measured results here after review. Mark synthetic smoke executions as tests, never scientific results. Freeze the design before final evaluation; later changes require a new experiment version. No final-test score is produced by this controller.
"""
    target.mkdir(parents=True, exist_ok=False)
    try:
        Archive(target / "feature_config.json").write(feature_config)
        Archive(target / "benchmark.json").write(benchmark_config)
        with (target / "experiment.ipynb").open("x", encoding="utf-8") as artifact:
            nbformat.write(notebook, artifact)
        with (target / "README.md").open("x", encoding="utf-8") as artifact:
            artifact.write(readme)
        pending = pending_blueprint_fields(target)
        load_benchmark_config(target / "benchmark.json", allow_generated_features=True)
        if not pending:
            load_research_config(target / "feature_config.json")
    except BaseException:
        shutil.rmtree(target)
        raise
    return ExperimentBlueprint(target, pending)