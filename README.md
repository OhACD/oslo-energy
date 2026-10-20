# Oslo Energy

An open-source research project studying electricity consumption forecasting in Norway. We aim to understand how input features and training-data quality affect predictions, and how machine-learning models compare with statistical forecasting methods and simple baselines.

The project currently provides the data pipeline for that research. It collects Statnett's public production and consumption data, preserves source snapshots, and prepares hourly observations for analysis. Despite the name, the active dataset covers **Norway as a whole**, not Oslo or the NO1 price area.

## Research Goals

- Measure how different feature sets change forecasts and prediction errors.
- Study the effect of missing, noisy, or otherwise lower-quality training data.
- Compare machine-learning models with statistical models and simple forecasting baselines under the same evaluation conditions.
- Publish reproducible experiments, findings, and model performance, including results where simpler methods perform better.

**One-hour-ahead consumption forecasting is the first task.** Seasonal-naive/persistence baselines and ordinary linear regression are implemented with shared benchmarking and modular plots. The [notebook-led validation experiment](experiments/002_baselines_linear_validation_v1/README.md) compared all methods on 8,784 hours in 2024: linear regression reduced MAE by 10.69% versus persistence. Its full notebook rerun reproduced the original metrics exactly. This is a provisional retrospective API-reference result, not operational accuracy. MLP and final-test scoring remain planned.

## Current Status

The data pipeline supports hourly API collection, immutable raw JSON archives, DST-safe normalization, quality reports, offline replay, optional PostgreSQL storage with correction history, and incremental daily/reconciliation job modes. Standalone analytical cleaning implements diagnostics and a versioned `preserve_and_flag` policy; it does not run during ingestion.

```text
Statnett hourly API -> raw archive -> normalized CSVs + quality report
                                              -> optional PostgreSQL storage
                                              -> standalone cleaning + manifest
                                                   -> readiness audit
                                                   -> causal features + eligibility
                                                   -> benchmark + model states + plots
```

We are **awaiting answers from Statnett** about differences between its API and exported data, measurement units, and how observations are revised. These questions matter for interpreting values and evaluating historical forecasts. See the [source investigation](docs/statnett-source-investigation.md) for the evidence.

Normalized and cleaned data retain missing values and remain marked `training_ready: false`: consistent structure, successful storage, and cleaning do not establish measurement accuracy or suitability for training. Basic lag/calendar [feature engineering](docs/features.md) has explicit issue-time availability checks. [Initial models and benchmarking](docs/benchmarking.md) enforce train-only fitting/scaling, fixed-reference scoring, and coverage reporting.

The standalone [research-readiness audit](docs/research-readiness.md) verifies a cleaned snapshot against explicit research windows and reports coverage, missing stretches, descriptive statistics, and source limitations without changing measurements or approving training.

Experiment 002 owns its [feature configuration](experiments/002_baselines_linear_validation_v1/feature_config.json), [benchmark configuration](experiments/002_baselines_linear_validation_v1/benchmark.json), and [notebook controller](experiments/002_baselines_linear_validation_v1/experiment.ipynb). Feature runs keep labels separate from model inputs and preserve every intended timestamp with availability flags. The former 001 feature-study directory has been consolidated into 002.

**Next:** add the MLP as a separately versioned notebook experiment. Provisional experiments use a fixed API reference with explicit source/revision limitations; they will not claim point-in-time operational accuracy. Source clarification remains necessary for stronger conclusions. The notebook controller runs validation only; standalone benchmark final-test scoring requires explicit opt-in. Each experiment keeps generated predictions, metrics, model states, plots, and executed notebooks together under its own `runs/` directory.

## Quickstart

Use **Python 3.10 or newer**. From the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev,modeling,notebooks]'
```

This installs the complete test, notebook, and benchmark interfaces, including scikit-learn and plotting. For data collection alone, `.[dev]` is sufficient. Neither PostgreSQL nor archived research data is needed to run the automated tests.

Fetch, archive and normalize hourly data:

```bash
python -m oslo_energy.pipeline.run_ingestion --from-date 2005-01-01 --normalize
```

This command contacts Statnett, saves a JSON snapshot under `data/raw/statnett/`, and writes a new run directory under `data/normalized/statnett/`. The normalized outputs are:

- `historical_candidates.csv`: hours that ended by the original fetch time, including missing measurements.
- `incomplete.csv`: hours that had started but not ended.
- `future_quarantine.csv`: hours starting after the original fetch time.
- `quality.json`: coverage, missing-value counts, and validation status.

The command prints the archive and output locations. The start date defaults to `2005-01-01`; omit `--normalize` to fetch and archive only. Generated data is ignored by Git. **Docker and PostgreSQL are not required for this workflow.**

See the [pipeline guide](docs/pipeline.md) for replay, options, and troubleshooting, or the [PostgreSQL guide](docs/persistence.md) for optional storage.

Clean an existing hourly normalization run offline, preserving every value:

```bash
python -m oslo_energy.pipeline.run_cleaning \
    --input-dir data/normalized/statnett/<normalization_run_id> \
    --policy preserve_and_flag
```

Replace the placeholder with the chosen run directory. Outputs go to a new directory under `data/cleaned/statnett/`, with findings, a report, and a checksum manifest. Warnings do not imply execution failure or training approval. See [cleaning](docs/cleaning.md) for the contract and verification.

With PostgreSQL configured and migrations applied, preview or run incremental collection:

```bash
python -m oslo_energy.pipeline.run_ingestion --daily --dry-run
python -m oslo_energy.pipeline.run_ingestion --daily
python -m oslo_energy.pipeline.run_ingestion --reconcile
```

Daily mode catches up and refreshes three completed Oslo dates. Reconciliation repairs gaps/nulls and refreshes 90 days. Both automatically archive, normalize, and persist in bounded chunks. An empty database backfills from 2005 unless `--start-date` is supplied. See [incremental jobs](docs/pipeline.md#incremental-jobs) for policies and scheduling; no schedule is installed automatically.

## Tests Versus Experiments

The experiment notebook is the interactive controller, JSON files are authoritative settings, package APIs contain the tested implementation, and CLI entry points support automation. Software tests and forecasting experiments serve different purposes:

| Interface | Command | What it does |
| --- | --- | --- |
| Full offline test suite | `python -m pytest -q` | Checks correctness with synthetic fixtures; no real-data experiment or live API/database access |
| Feature and leakage checks | `python -m pytest -q tests/transformation/test_feature_families.py tests/pipeline/test_features.py` | Tests causal lags, DST, missing inputs, and label/feature separation |
| Model and benchmark checks | `python -m pytest -q tests/models tests/benchmarking` | Fits synthetic models and checks train-only scaling, metrics, checkpoints, and plots |
| Notebook smoke check | `python -m pytest -q tests/pipeline/test_experiment_notebook.py` | Executes the actual controller in a fresh kernel against tiny synthetic settings, not real-data results |
| Full notebook experiment | `python -m oslo_energy.pipeline.run_experiment --experiment-dir experiments/002_baselines_linear_validation_v1` | Rebuilds audit/features from pinned cleaned data, trains/scores validation, displays plots, and saves the executed notebook |
| Real-data comparison | `python -m oslo_energy.pipeline.run_benchmark --config path/to/benchmark.json` | Fits on configured training data and scores the selected evaluation split |

PostgreSQL integration tests are separately opt-in. For additional syntax checks, run `python -m compileall -q src/oslo_energy`. Passing software tests is not evidence of predictive accuracy or source correctness.

### Reproduce The Existing Validation Experiment

Open [experiment.ipynb](experiments/002_baselines_linear_validation_v1/experiment.ipynb), choose the project environment, restart the kernel and Run All. To execute the same notebook headlessly in a fresh project-interpreter kernel:

```bash
python -m oslo_energy.pipeline.run_experiment \
    --experiment-dir experiments/002_baselines_linear_validation_v1
```

This starts from the pinned cleaned snapshot, writes a new audit and feature run, and evaluates validation. Source configs are snapshotted and unchanged; explicit resolved copies inside the execution link each newly generated stage. Results live under `runs/experiment_<id>/`, including the executed notebook and benchmark's plots. There is no ingestion or test scoring in this command.

For benchmark-only reproduction, if the exact historical feature run referenced by [the frozen benchmark config](experiments/002_baselines_linear_validation_v1/benchmark.json) exists locally:

```bash
python -m oslo_energy.pipeline.run_benchmark \
    --config experiments/002_baselines_linear_validation_v1/benchmark.json
```

This trains/scales on 2018-2023 and evaluates 2024. It does not score the 2025 test. Every invocation creates a new execution; it does not overwrite or reuse a fitted model implicitly.

**A fresh checkout does not contain the referenced datasets or generated runs.** The notebook needs the pinned cleaned artifacts; the benchmark-only command needs the historical feature run. A newly fetched cleaned dataset is a different input, even when dates or values appear similar; do not bypass checksum mismatches or rewrite the frozen experiment to point at it. The controller's explicit generated-feature rebinding is recorded in each execution, not substituted into source configs. See the [experiment record](experiments/002_baselines_linear_validation_v1/README.md) for requirements, or create a new version using the workflow below.

## End-To-End Workflow

Run from the repository root with the environment activated. This is a sequence of explicit stages, not one automatic command. Replace quoted placeholder paths with the actual run directories printed by each command. **Only collection contacts Statnett; the later stages run offline without PostgreSQL.**

1. **Collect and normalize a bounded hourly snapshot.** This example includes the candidate research years and earlier lag history:

   ```bash
   python -m oslo_energy.pipeline.run_ingestion \
       --from-date 2005-01-01 --to-date 2025-12-31 --normalize
   ```

   Inspect the new normalized run's `quality.json`, including requested coverage and missingness. Existing archives can instead be replayed with their original fetch timestamp as described in the [pipeline guide](docs/pipeline.md#replay-a-snapshot). Never substitute today's timestamp for the original fetch time.

2. **Create the preservation-first cleaning run.** Use the exact normalized directory printed above:

   ```bash
   python -m oslo_energy.pipeline.run_cleaning \
       --input-dir 'data/normalized/statnett/<normalization_run_id>' \
       --policy preserve_and_flag
   ```

3. **Audit explicit candidate research windows.** Use the newly printed cleaned directory:

   ```bash
   python -m oslo_energy.pipeline.run_research_audit \
       --input-dir 'data/cleaned/statnett/<cleaning_run_id>' \
       --window train:2018-01-01:2023-12-31 \
       --window validation:2024-01-01:2024-12-31 \
       --window test:2025-01-01:2025-12-31
   ```

   Review missing stretches, source limitations, and descriptive findings. This audits the candidate test window; it does not train or score models on it. Do not tune filters or features using held-out outcomes. If that inspection drives substantive design changes, choose a fresh final test and disclose why. These dates are examples supplied as settings, not hardcoded split boundaries.

4. **Define a new versioned experiment.** Use 002's [feature configuration](experiments/002_baselines_linear_validation_v1/feature_config.json), notebook, and [feature guide](docs/features.md#shared-configuration-and-commands) as the schema reference. Set cleaned and audit references, matching windows, task/availability assumptions, family versions, and preservation rules. Relative input paths resolve from the configuration's directory. Keep feature and model settings inside the same experiment directory.

   To inspect an artifact's checksum with the project's hashing API:

   ```bash
   python -c 'import sys; from pathlib import Path; from oslo_energy.pipeline.cleaning import artifact_checksum; print(artifact_checksum(Path(sys.argv[1])))' \
       'data/cleaned/statnett/<cleaning_run_id>/manifest.json'
   ```

   Repeat for the audit manifest. Choose a new experiment directory rather than modifying historical configs. No initializer currently fills these references automatically. Setting `training_ready` to true is not a configuration shortcut; provisional feature generation carries the unresolved restrictions forward.

5. **Build causal features from that configuration.** The linked audit must match both the cleaned manifest and the configured windows:

   ```bash
   python -m oslo_energy.pipeline.run_features \
       --config 'experiments/<new_feature_study>/config.json'
   ```

   Inspect the printed coverage and saved report. Targets and eligibility flags are separate from the model-input matrix. Changed windows require matching new audit evidence; audit also accepts this same configuration through `--config` after its references have been established.

6. **Define and run a new benchmark version.** Follow the [benchmark configuration schema](docs/benchmarking.md#benchmark-configuration-and-cli). Pin the newly generated feature manifest and select `seasonal_naive`, `persistence`, and `linear_regression`, explicit baseline IDs, `evaluation_split: validation`, and `plots: [comparison]`. `provisional_research: true` records the permitted retrospective scope, not source certification.

   ```bash
   python -m oslo_energy.pipeline.run_benchmark \
       --config 'experiments/<new_benchmark_study>/benchmark.json'
   ```

   The linear model and scaler fit training examples only. Baselines do not fit. No missing labels are filled, no final-test scores are produced, and source publication/revision limitations remain explicit. Final-test scoring requires a separately frozen design, `evaluation_split: test`, and deliberate `--allow-test-evaluation`; do not use it as a routine smoke check.

### Find And Verify Results

By default, a benchmark keeps generated outputs beside its configuration:

```text
experiments/<experiment>/
    benchmark.json
    runs/benchmark_<execution_id>/
        predictions.csv
        metrics.json
        benchmark_report.json
        models/<model_id>/state.json
        plots/benchmark_comparison.png
        manifest.json
```

The CLI prints the run path. The comparison plot shows common-timestamp MAE/RMSE, coverage, and paired baseline-relative skill, not a generic percentage accuracy. Model failures remain visible. `--output-dir` selects another output root; previous executions are never overwritten. Generated runs are ignored by Git and must be retained separately when preserving a research result.

Verify integrity and recompute metrics from saved predictions:

```bash
python - <<'PY'
from pathlib import Path
from oslo_energy.ingestion.archive import Archive
from oslo_energy.pipeline.benchmark import validate_benchmark_run, recompute_benchmark_metrics

run = Path("experiments/<experiment>/runs/<benchmark_execution_id>")
validate_benchmark_run(run)
assert recompute_benchmark_metrics(run) == Archive(run / "metrics.json").read()
print("Artifact integrity and metric recomputation verified")
PY
```

For a programmatic interface, use `load_feature_matrix` for X and `load_model` for saved model states; labels are loaded separately. See [features](docs/features.md#artifacts-and-safe-model-inputs) and [benchmarking](docs/benchmarking.md#outputs-failures-and-reloading) for the contracts and model reload semantics.

## Documentation

| Guide | Read it to... |
| --- | --- |
| [Research Plan](docs/research-plan.md) | Understand the questions, planned experiments, and publication goals |
| [Pipeline](docs/pipeline.md) | Collect data, inspect outputs, replay snapshots, and understand normalization |
| [Cleaning Pipeline](docs/cleaning.md) | Run analytical cleaning and extend the versioned policy contract |
| [Research Readiness](docs/research-readiness.md) | Audit explicit research windows before selecting features or training models |
| [Features](docs/features.md) | Build versioned causal lag/calendar features and understand leakage checks |
| [Benchmarking](docs/benchmarking.md) | Use initial model adapters, train-only fitting, fixed-reference metrics, and saved comparisons |
| [PostgreSQL Storage](docs/persistence.md) | Configure optional storage and understand correction history |
| [Statnett Investigation](docs/statnett-source-investigation.md) | Review the source discrepancies, evidence, and open questions |

Implementation lives under [src/oslo_energy](src/oslo_energy/), with tests under [tests](tests/).

## Development and Contributions

```bash
python -m pip install -e '.[dev,modeling,notebooks]'
python -m pytest
python -m compileall -q src/oslo_energy
```

The full test suite needs the modeling and notebooks extras and runs without live API access or PostgreSQL. Learned-model and clean-kernel notebook tests use synthetic data only; they do not run the real-data experiment. Core data-pipeline commands do not require these extras. Database integration tests are opt-in; see the [PostgreSQL guide](docs/persistence.md).

Contributions are welcome, especially source-validation evidence, reproducible data-quality analyses, tests, and documentation improvements. For features or models, discuss the forecasting task and evaluation design in an issue before building a new experiment. See the [research plan](docs/research-plan.md) for the current direction.

## License and Sources

Project source code and original documentation are covered by the [MIT License](LICENSE). That license does not apply to Statnett data, its API, or third-party material, including the archived examples. Confirm applicable provider terms before redistribution or production use.

- [Statnett operational data](https://driftsdata.statnett.no/)
- [Statnett timestamped downloads and source definitions](https://driftsdata.statnett.no/Web/Download/)
