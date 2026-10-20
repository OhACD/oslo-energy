# Baselines And Linear Regression: Validation V1

**Scope frozen before first execution on October 20, 2026.** This experiment compares one-hour Norwegian consumption forecasts against a fixed retrospective API reference. It is a validation comparison, not a final-test result or operational certification.

The model/data design is unchanged. The experiment now owns its [notebook controller](experiment.ipynb), [feature settings](feature_config.json), and [benchmark settings](benchmark.json). The redundant 001 source directory was removed; historical source versions remain in Git and all generated artifacts were preserved.

## Fixed Design

- Training target dates: January 1, 2018 through December 31, 2023, inclusive in `Europe/Oslo`.
- Evaluation target dates: January 1 through December 31, 2024, inclusive in `Europe/Oslo`.
- Target consumption interval `[t, t+1h)`, forecast issued at `t`.
- Inputs: elapsed consumption lags 1/24/168 hours and cyclic Oslo hour/day-of-week, from the exact feature manifest pinned in [benchmark.json](benchmark.json).
- Seasonal naive uses lag 168 only; persistence uses lag 1 only; neither fits or falls back.
- Linear regression fits StandardScaler and ordinary least squares, with intercept, on training data only. No tuning, imputation, anomaly filtering, validation refit, or changed feature recipe.
- Preserve intended timestamps. Report coverage and both individual and shared-timestamp MAE/RMSE; compare paired MAE skill against each baseline.
- Do not score the 2025 final test in this experiment. Descriptive inspection of that candidate period has already occurred and must be disclosed in later study designs.

The feature snapshot is local, ignored third-party data, not included in this repository. The reference is not independently verified truth. Zero publication delay is explicitly assumed; actual provider availability and historical revisions remain unresolved. Dataset `training_ready` stays false and upstream review remains pending. This run authorizes only provisional retrospective research.

## Reproduce

Install the modeling and notebook extras:

```bash
python -m pip install -e '.[dev,modeling,notebooks]'
```

Open [experiment.ipynb](experiment.ipynb), select the project environment, restart the kernel, and Run All. Or execute the same notebook in a fresh project-interpreter kernel from the repository root:

```bash
python -m oslo_energy.pipeline.run_experiment --experiment-dir experiments/002_baselines_linear_validation_v1
```

This starts from the exact pinned cleaned snapshot, not live ingestion. It writes a new audit, rebuilds features, trains the scaler/OLS, evaluates validation, renders plots, verifies all saved model reloads, and publishes the execution. No random splitting, test scoring, MLP, or historical-artifact deletion occurs.

Each notebook execution creates a new `runs/experiment_<id>/` directory containing:

| Artifact | Purpose |
| --- | --- |
| `sources/` | Original config and notebook snapshots |
| `config/` | Explicit run-local resolved configs |
| `audit/research_audit_<id>/` | New readiness audit |
| `features/features_<id>/` | Rebuilt causal features |
| `benchmark/benchmark_<id>/` | Predictions, metrics, models, plots, and stage manifest |
| `experiment_report.json` | Combined execution checks and stage locations |
| `manifest.json` | Experiment-stage checksums |
| `experiment.executed.ipynb` | Executed notebook saved by the clean-kernel runner |
| `notebook_execution.json` | Notebook completion and checksum record |

Source JSON files remain authoritative and unchanged. The controller explicitly binds a new audit into the run-local feature config and the new feature manifest into the run-local benchmark config. These are visible derived settings, not hidden notebook parameter overrides. All settings and stage artifacts are checksummed. Failed stages do not publish a completed experiment; a failed notebook is marked failed separately. Generated runs are ignored by Git.

The source `benchmark.json` still pins the original feature run, so the older benchmark-only command remains available from this directory:

```bash
python -m oslo_energy.pipeline.run_benchmark --config benchmark.json
```

That command creates a separate `runs/benchmark_<id>/` and does not rebuild audit/features. The notebook is the end-to-end controller. Fresh reproduction requires the pinned cleaned artifacts; it does not require the old feature output because a new one is generated. Standalone feature generation still requires the audit referenced by its source config.

Keep this configuration unchanged. New settings or evaluation choices require a new version. The [plotting contract](../../docs/benchmarking.md) allows new registered plot types without changing estimators or scoring. Model comparisons use regression errors and coverage, not an unexplained percentage called accuracy.

## First Execution Results

Executed October 20, 2026. Run ID: `benchmark_78e2f4c89c5549d2a0d06d7a1839c019`. Frozen configuration SHA-256: `5e720423fef27466deb7c974be3b30b2b493511a96bac0089b229b437e5d8cc4`.

OLS and its scaler fitted 52,584 training examples. Fit cutoff was `2023-12-31T23:00:00Z`, equivalent to January 1, 2024, 00:00 in Oslo. All methods predicted and scored the same 8,784 validation hours, with no missing predictions or labels and no estimator failures.

| Method | MAE | RMSE | Prediction / Scored Coverage |
| --- | ---: | ---: | ---: |
| Seasonal naive, 168 elapsed hours | 1,014.02 | 1,397.45 | 100% / 100% |
| Persistence, 1 elapsed hour | 261.79 | 363.42 | 100% / 100% |
| Linear regression | 233.81 | 307.94 | 100% / 100% |

Errors are in unverified provider units. Linear regression reduced paired MAE by **10.69% versus persistence** and **76.94% versus seasonal naive**. Persistence is the stronger reference for this one-hour task; the large improvement over weekly naive alone would overstate the incremental benefit. These results establish only this retrospective 2024 comparison, not universal superiority or operational accuracy.

Local generated artifacts are under `runs/benchmark_78e2f4c89c5549d2a0d06d7a1839c019/`, including `plots/benchmark_comparison.png`. Run checksums, exact recomputation of saved metrics, and reloaded OLS prediction parity were verified. The plot was visually inspected. Dependency versions and source hashes are recorded in the run manifest; retain this directory to reproduce the published local result. Generated runs are not included in Git or a fresh checkout.

The 2025 final-test scores remain uncomputed. No hyperparameter adjustment, feature change, imputation, or MLP training was performed after observing these validation scores. The next experiment must be separately versioned if settings or models change. Dataset certification remains false; source/publication/revision limitations and upstream pending review remain in force.

## Notebook Rerun

The consolidated notebook was executed end to end on October 20, 2026, in a fresh `.venv` Python 3.10 kernel. Execution: `experiment_4a0667611d2b476fb5c78c6b46538218`.

- Audit: `audit/research_audit_f8328578707d4fd6a2bf974b7809e242`.
- Rebuilt features: `features/features_e32ec480637a433087bc8c0545e8966c`.
- Benchmark: `benchmark/benchmark_4e3fc12a96ba4b068fcf3889ff473b63`.
- Executed notebook: `runs/experiment_4a0667611d2b476fb5c78c6b46538218/experiment.executed.ipynb`.

All metrics exactly matched the first execution's values above; all three model reloads reproduced saved predictions. Source configs were not edited. The original benchmark run remains intact, and the final test remains unscored. The wrapper CLI recorded notebook completion/checksums separately from the experiment-stage manifest. Use `validate_notebook_execution` from [the notebook runner](../../src/oslo_energy/pipeline/run_experiment.py) to verify both layers. An interactive Run All creates the stage manifest; the wrapper additionally captures a persisted executed-notebook artifact.
