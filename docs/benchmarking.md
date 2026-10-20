# Benchmarking And Initial Models

The benchmark foundation implements unfitted weekly seasonal naive and previous-hour persistence, plus ordinary linear regression with training-only standardization. A shared evaluator compares predictions on fixed timestamps and observed reference labels. The [first validation experiment](../experiments/002_baselines_linear_validation_v1/README.md) has run and been reproduced by its full notebook controller from pinned cleaned data. MLP and final-test scoring remain future work.

## Install And Test

```bash
python -m pip install -e '.[dev,modeling]'
python -m pytest -q tests/benchmarking tests/models
```

The optional modeling extra provides scikit-learn and Matplotlib for benchmark diagrams. Core ingestion, cleaning, feature generation, and unfitted adapters do not import scikit-learn until a learned model is used. Plotting imports Matplotlib only during rendering. CI installs this extra so learned-model and chart tests actually execute rather than silently skipping.

The tested local stack uses Python 3.10, scikit-learn 1.7.2, and SciPy 1.14.1. SciPy 1.15.3's PROPACK extension failed to load in the local macOS ARM environment; the extra constrains SciPy to `>=1.14,<1.15` on that platform. This records the observed compatibility workaround, not a claim that all newer SciPy wheels fail everywhere. Generated manifests record actual software versions.

## Model Contracts

| Model ID | Required inputs | Fit behavior |
| --- | --- | --- |
| `seasonal_naive` | `consumption_lag_168h` | None; predict exactly that lag |
| `persistence` | `consumption_lag_1h` | None; predict exactly that lag |
| `linear_regression` | All columns in the selected feature recipe | Fit `StandardScaler` and scikit-learn `LinearRegression` together on eligible training rows |

Both baselines have no fallback, drift, averaging, or implicit filling. An unrelated missing feature does not prevent a baseline forecast. The linear adapter accepts boolean `fit_intercept`, default true; the intercept setting applies to the regression on standardized predictors. This is ordinary least squares, not Ridge. No target scaling or regularization is added.

Each adapter implements stable ID/version, validated configuration, required columns, guarded training, indexed prediction, and JSON persistence. The [model package](../src/oslo_energy/models/) separates contracts, base execution, registry, and concrete adapters. New methods require an adapter, registration, and model-specific tests, not scoring logic in the estimator.

`TrainingBatch` contains only complete required X columns and observed labels with aligned training timestamps. `ForecastBatch` contains X and issue times only, never labels, split-selection flags, audit statistics, or retrospective target availability. The verified [data handoff](../src/oslo_energy/benchmarking/data.py) recomputes eligibility, checks registered feature schemas, cross-checks the full configured target grid and split assignments, and validates saved per-feature availability evidence.

## Temporal Fit Gates

- Fit cutoff is the first validation forecast issue time, even when a later explicitly permitted test evaluation is requested.
- Training targets must belong to the configured training window, not merely carry a user-provided `train` label.
- Each training label must satisfy `target_end_utc + configured_publication_delay <= fit_cutoff`. A recently completed but not-yet-available training label is excluded.
- Required inputs and labels must be finite/nonmissing; eligibility filtering occurs only in the training batch. Intended prediction timestamps remain unchanged.
- Scaler and regression fitting receive only this validated batch. No validation/test fit, imputation, hyperparameter search, checkpoint selection, or train-plus-validation refit is performed here.
- Learned predictions before the recorded fit cutoff, altered input column order, invalid training roles, and mismatched timestamps are rejected.

The feature pipeline's [causality contract](features.md) remains in force. Valid saved measurement features must have availability evidence no later than their issue time; the benchmark checks this again before fitting. The test uses sequential one-hour forecasts: already elapsed validation/test observations can supply later forecasts' causal lags, but they never update fitted scaler/model parameters.

These checks do not recover unknown historical Statnett publication times or pre-capture revisions. Configured publication delay remains an explicit retrospective assumption. `training_ready` remains false and upstream eligibility remains `pending_review`; provisional execution permission is run-level scope, not source certification.

## Shared Scoring Rules

Every selected model returns a float prediction or explicit `unavailable_input`/`failed` status on the same intended timestamp grid. Missing required inputs produce no prediction, not zero or an invented estimate. A missing target does not suppress a prediction, but it cannot contribute to error metrics.

The evaluator rejects extra, duplicate, reordered, or misaligned timestamps rather than silently intersecting different tasks. It reports:

- Intended, predicted, reference-available, scored, unavailable-input, and failed counts.
- Prediction coverage and scored coverage, both with intended forecasts as denominator.
- Individual MAE/RMSE on each model's finite predictions and observed reference labels.
- Pairwise model/baseline errors on the same common eligible timestamps, with paired counts and MAE skill `1 - paired_model_MAE / paired_baseline_MAE`.
- An additional all-model-common comparison, without replacing individual coverage or pairwise results.

If no rows can be scored, errors are null rather than NaN or a misleading zero. Relative skill is null with explicit `zero_baseline_mae` when the paired baseline error is zero; no pairs use `no_pairs`. Because coverage differs by model, comparing only individual MAEs can reward abstention. Retain the coverage and paired comparisons.

Reference values remain in unvalidated provider units. No unexplained accuracy percentage, automatic MAPE, significance claim, or independent-ground-truth assertion is produced.

## Benchmark Configuration And CLI

Use a separate version-1 JSON benchmark configuration. Do not add model fields to an existing feature-generation config or rewrite old feature runs. Example template:

```json
{
  "schema_version": "1",
  "benchmark_id": "initial_comparison_v1",
  "feature_run": {
    "path": "../../data/features/statnett/<feature_run_id>",
    "manifest_sha256": "<exact 64-character feature manifest SHA-256>"
  },
  "models": [
    {"id": "seasonal_naive", "version": "1", "configuration": {}},
    {"id": "persistence", "version": "1", "configuration": {}},
    {"id": "linear_regression", "version": "1", "configuration": {"fit_intercept": true}}
  ],
  "baselines": ["seasonal_naive", "persistence"],
  "provisional_research": true,
  "evaluation_split": "validation"
}
```

Replace placeholders with exact artifact references before using this template. Paths resolve relative to the benchmark config. Split boundaries and feature definitions come from that pinned feature run; the benchmark does not independently redefine them. Unknown keys/models/versions/settings and learned models mislabeled as baselines fail. `provisional_research: true` explicitly permits only retrospective research with the inherited limitations; it does not resolve outstanding source questions.

For a notebook-led experiment, the [blueprint generator](../README.md#create-a-new-experiment) instead writes `"feature_run": {"source": "experiment_features"}`. This is an explicit controller-only reference to the freshly generated stage, not a dummy checksum or automatic latest-run selection. The controller resolves it to a real path/hash inside the execution before invoking the benchmark. The standalone benchmark loader rejects this symbolic form; its input must remain fully pinned. Original pinned configurations remain supported unchanged.

The command below starts an actual fit and scoring run. It is not part of normal synthetic tests:

```bash
python -m oslo_energy.pipeline.run_benchmark --config path/to/benchmark.json
```

Omitting `evaluation_split` defaults to validation. Configuring `test` additionally requires explicit `--allow-test-evaluation`; without it the run fails before creating output. Final-test scoring should happen only after the model/recipe/settings are frozen, not during routine development. Default validation execution does not parse test target values into numeric references or fit inputs, and does not output test metrics. The candidate test period has already undergone descriptive inspection; disclose any influence on experimental choices and choose a fresh test if those outcomes drive tuning.

## Outputs, Failures, And Reloading

Each run defaults to `runs/benchmark_<id>/` beside its benchmark configuration, keeping experiment outputs together. An explicit `--output-dir` overrides that root, for example `data/benchmarks/statnett`. Only the designated `runs/` subtree may receive outputs beneath an experiment; its configuration/source files and earlier executions are never overwritten. Generated experiment runs are ignored by Git.

Outputs are `predictions.csv`, `metrics.json`, `benchmark_report.json`, successful adapters' `models/<model_id>/state.json`, selected `plots/*.png`, and a completion `manifest.json` written last. Plots are checksummed artifacts, not screenshots detached from their input metrics. The predictions table stores the evaluation reference for metric recomputation; it is not an X matrix.

Manifests record pinned input/config identities, output checksums, effective settings/fingerprint, implementation hashes, runtime/dependency versions, and execution ID/time. Model state includes fitted feature order, training interval/index checksum, row counts, fit cutoff, label-availability bounds, and parent feature identity. Baseline states explicitly say no fit is required. Scaler and regression state are saved together as dimension-checked finite JSON, not executable pickle. Reloading currently requires the same scikit-learn version recorded in the checkpoint. Integrity hashes are not signed authenticity guarantees.

Use `validate_benchmark_run` before inspecting saved results and `recompute_benchmark_metrics` to reproduce metrics from predictions/reference/statuses. `load_model` in [the model registry](../src/oslo_energy/models/registry.py) reconstructs registered adapters from JSON; verify the enclosing benchmark manifest before loading its state. Roundtrip tests check numeric prediction parity and temporal/schema guards.

A single estimator failure produces failed statuses on every intended timestamp for that method, retains other methods' results, writes `completed_with_model_failures`, and returns CLI code `1`. It is not silently removed from comparisons. Required input/config/contract failures and publication failures return nonzero without a completed output; failed writes remove only the new execution directory. Complete runs with inherited source warnings return `0`, not training approval. Argument errors use argparse's `2`.

## Modular Experiment Plots

The [plotting package](../src/oslo_energy/benchmarking/plotting/) is separate from model adapters and metric computation. `PlotContext` contains metrics, evaluation predictions, and title; `BenchmarkPlot` supplies stable `plot_id`, version, filename, and isolated `render` execution. Renderers implement `_render` and must produce a new PNG without altering benchmark evidence.

Optional benchmark configuration field `"plots": ["comparison"]` selects registered plot IDs; omission defaults to that selection and an empty list disables plots. Unknown or duplicate IDs fail. The runner records plot definitions and output paths in the report and manifests their checksums. Existing older benchmark runs with no chart, or the initial root-level comparison image, remain verifiable.

The `comparison` renderer writes `plots/benchmark_comparison.png` with four panels: MAE and RMSE on all-model-common reference timestamps, prediction/scored coverage, and pairwise MAE skill against each baseline with sample counts. A failed method can leave no all-model-common timestamps; the diagram reports that explicitly rather than displaying zero error. The chart does not fit models, alter sample masks, or choose a winner for later tuning.

To add a renderer, implement a new `BenchmarkPlot` module, add its class to [the explicit registry](../src/oslo_energy/benchmarking/plotting/registry.py), and test its metric/plot semantics and output isolation. Select its ID in the next experiment config. No estimator or evaluator changes are required. Future prediction-vs-reference and residual plots belong here, not inside model classes. Model errors and relative skill are not generic percentage accuracy.

## Current Verification And Next Work

Synthetic tests cover known OLS recovery, train-only scaler statistics, held-out perturbation invariance, delayed-label cutoff, model-state roundtrip, baseline missingness/no fallback, grid/schema rejection, future availability rejection, failure counts, metric recomputation, input/config mutation, and final-test opt-in. Existing feature/cleaning/audit checks remain unchanged.

The first frozen validation comparison used 52,584 training examples and the same 8,784 scored 2024 hours for all methods. OLS MAE was 233.81 versus persistence 261.79 and seasonal naive 1,014.02 in provider units, a 10.69% paired MAE reduction against the stronger persistence reference. The clean-kernel notebook rerun rebuilt audit/features, refitted the same models, and exactly reproduced these metrics. See the [experiment record](../experiments/002_baselines_linear_validation_v1/README.md) for scope, artifacts, verification, and limitations. Final-test scores are still uncomputed; MLP remains next work.
