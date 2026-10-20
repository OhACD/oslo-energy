# Research Plan

Oslo Energy aims to study **Norwegian electricity consumption forecasting** through reproducible comparisons of features, training-data quality, and forecasting methods. This document describes planned work, not implemented models or published results.

The current dataset covers national production and consumption. It does not support claims about Oslo-specific demand. One-hour-ahead consumption is the initial task, comparing simple baselines, ordinary linear regression, and a small feed-forward neural network. Additional horizons and data sources remain future decisions.

## Research Questions

1. Which input features improve forecasts, and does their usefulness change with the prediction horizon or season?
2. How do missing observations, noise, and other training-data limitations affect predictions and forecast errors?
3. Under comparable conditions, when do machine-learning models outperform statistical models and simple baselines, and when do they not?

The goal is to explain these differences, not assume that more features, cleaner-looking data, or more complex models will perform better.

## Where We Are Now

The [data pipeline](pipeline.md) collects and archives hourly observations, preserves missing values, and supports replay and optional database storage. A separate [cleaning stage](cleaning.md) implements diagnostics, an extensible policy contract, and `preserve_and_flag`. Basic [lag/calendar features](features.md), shared research configuration, and the [baseline/linear benchmark foundation](benchmarking.md) are implemented. The [first real-data validation experiment](../experiments/002_baselines_linear_validation_v1/README.md) has completed and been rerun end to end through its notebook, reproducing the original metrics. MLP and final-test scoring remain future work.

A standalone [research-readiness audit](research-readiness.md) now verifies completed cleaning artifacts and profiles explicit candidate research windows. It reports endpoint/internal coverage, nulls and unavailable stretches, descriptive statistics, and source-context overlaps. It never changes observations or automatically approves research eligibility. Review its material findings and record a task-specific decision before real-data feature selection or model training.

We are awaiting Statnett's answers about measurement units, API/export disagreement, and revisions. The [source investigation](statnett-source-investigation.md) records the evidence. Until those questions and the training-data policy are addressed, normalized observations remain marked `training_ready: false`.

Provisional retrospective experiments may proceed against an explicitly frozen API snapshot while these questions remain unresolved. Results describe agreement with that reference, not independent ground truth or a point-in-time operational backtest. Immediate availability after an hour ends must be disclosed as an assumption, not a confirmed publication guarantee. Cleaning and experiment execution do not promote upstream data to `training_ready: true`.

## Planned Work

| Stage | Main outcome |
| --- | --- |
| Resolve source questions | Document measurement meaning, known limitations, and a defensible data-selection policy |
| Define the forecasting task | Specify target, time resolution, forecast issue time, horizons, and evaluation periods |
| Review research readiness | Audit a frozen cleaned snapshot over explicit windows; document missingness handling, material findings, and provisional research limitations |
| Engineer features | Build reproducible transformations that use only information available when a forecast is made |
| Establish comparisons | Implement simple baselines, suitable statistical methods, and a focused set of ML models |
| Run experiments | Measure feature and training-quality effects under consistent evaluation conditions |
| Publish findings | Release methods, reproducible configurations, performance results, and limitations |

**Review the readiness audit before selecting real-data features.** Feature engineering then builds on the audited preservation policy and an explicit task-specific selection rule. Initial families are consumption lags of 1, 24, and 168 elapsed UTC hours and cyclic local hour/day-of-week encodings. Rolling summaries, holidays, weather, and production remain later families. Weather or production features require additional availability checks: future observed weather and same-hour production cannot simply be treated as known at forecast time. Cleaning-policy comparisons should be evaluated separately from feature-set comparisons, with the evaluation reference held fixed.

The initial neural network will be a small feed-forward MLP, not an LSTM. Architecture, split boundaries, and tuning budgets must be frozen before final test evaluation; no model is presumed to outperform a baseline.

## Experiment Design

### Experiment Sequence

Begin with a manageable strategy-by-model comparison using one fixed, versioned initial feature recipe containing the feature families available at that stage. This is not a commitment to every future feature. Use the same normalized snapshot, target, horizons, chronological splits, forecast issue times, and evaluation reference across treatments. Feature definitions stay fixed, while their resulting values and usable training-example counts may change.

Next, compare feature-family ablations and combinations under fixed strategies. Investigate selected strategy-by-feature and strategy-by-model interactions after those comparisons, rather than immediately running every possible combination. Choose configurations on chronological validation folds and reserve an untouched final test for the selected comparisons. Conclusions should identify the tested task, horizons, models, periods, and reference; they do not establish a universally best energy-forecasting strategy.

### Feature Sets

Compare feature sets using the same dataset version, training windows, forecasting methods, and test periods. Add or remove groups of features to measure their contribution, rather than changing the data and model at the same time.

Record how each feature is calculated, how much history it needs, and when its inputs become available. Measure changes in both predictions and error, including whether improvements are consistent across horizons and seasons.

Feature recipes should specify null handling and minimum valid coverage for statistical windows. Preserve UTC hour identity when constructing lags: excluded or absent rows must not compress elapsed time. Local dates remain useful for calendar features and grouping. Calendar encodings, Norwegian holiday indicators, and statistical feature families require their own design; they are not cleaning rules.

The implemented `FeatureFamily` contract declares stable identity/version, validated settings, required inputs, output schema, lookback, forecast availability, and a timestamp-aligned result with validity/lineage. A registry and versioned recipe select families without changing orchestration. This is a separate contract from `CleaningPolicy`. Measurement access is restricted through a causal-history accessor; target/future perturbation tests guard the initial families. Actual publication times and revisions remain separate unresolved limitations. Learned preprocessing belongs in a future fold-scoped fit/transform contract, not in globally fitted families or cleaning outputs.

### Training-Data Quality

Keep the feature set and evaluation reference fixed while changing the training data. Define quality dimensions explicitly: missingness, noise, suspicious measurements, historical coverage, and revisions are different problems.

Distinguish naturally occurring source issues from deliberately introduced corruption. Synthetic missingness or noise can reveal sensitivity, but does not necessarily represent Statnett's actual data problems. Likewise, imputation and exclusion are experimental choices, not automatically improvements.

Use the reusable `preserve_and_flag` output as an initial baseline, then identify downstream treatments explicitly. `complete_cases` excludes examples missing task-required inputs or labels; it does not fill values. Learned imputers fit separately inside each training fold. Observation-level imputation before lag generation and feature-level imputation afterward are distinct recipes, with their order recorded. Combined treatments use stable strategy IDs, versions, ordered steps, configuration, and seeds rather than silently chained cleaning policies.

Controlled degradation writes a separate experimental branch and records affected fields, partitions, and synthetic modifications. Training-only corruption and degraded forecast inputs are different conditions. Keep evaluation labels unaltered by default, even when inputs are deliberately degraded.

Evaluate every training-quality condition against the same documented, held-out reference. API/export disagreement does not establish which product is correct. If independent ground truth is unavailable, report performance against the chosen reference and disclose that limitation.

Start with separate feature and quality experiments. Study their interactions later if the initial results justify the additional scope.

### Forecasting Methods

Compare a small, purposeful set of methods before expanding the benchmark:

- **Simple baselines:** for example, using the latest available observation or a comparable previous seasonal period.
- **Statistical models:** time-series methods suited to the selected resolution and seasonal structure.
- **Machine-learning models:** methods selected for the task and available data, rather than complexity alone.

The first complete benchmark will compare a 168-elapsed-hour seasonal-naive forecast (plus previous-hour persistence as a sanity reference), scikit-learn ordinary linear regression, and a small PyTorch MLP. Baseline and linear adapters plus shared scoring are implemented; MLP remains future work. The learned models will share the same lag/calendar recipe and eligibility rules. Model adapters standardize guarded fit, indexed prediction, and fitted-state serialization without sharing estimator internals. The linear adapter fits its scaler and ordinary least-squares estimator on training examples only. Ridge and more complex architectures are separate future experiments.

The benchmark runner uses a separate pinned feature-run configuration, defaults to validation scoring, and requires explicit final-test opt-in. It compares each method's coverage and errors as well as paired common-timestamp errors and baseline-relative skill. Missing labels are never filled and model failures remain visible. Its first real-data execution trained on 2018-2023 and evaluated 2024 without tuning or test scoring. Follow-up model/feature choices require separately versioned experiments; these validation results are not the final held-out result.

Use the same forecast horizons, test dates, information availability, and stated retraining policy. Document tuning budgets and computational cost so the comparison is interpretable.

## Evaluation Principles

Use chronological train/validation/test splits and evaluate forecasts at successive points in time, each using only the history available then. Reserve a final test period that is not used to select features, models, or hyperparameters.

Fit preprocessing, imputation, scaling, and feature selection only on the training portion of each evaluation split. Check that rolling features do not include their prediction target or future observations. The [cleaning contract](cleaning.md) treats cleaned observations as inputs to feature engineering, not as a feature matrix or proof of training eligibility.

Do not silently fill missing target labels and treat them as observations. Define one shared rule for excluding unavailable reference labels from scoring, independent of the strategy. Keep the intended forecast timestamps fixed; a smaller training set does not redefine the test task. Report attempted forecasts, scored forecasts, unavailable predictions, failures, and coverage alongside error metrics. Where appropriate, report paired errors on common eligible timestamps, while retaining coverage results so abstention or selection bias remains visible.

Revised historical measurements introduce a separate limitation: today's archive may contain information that was not available when a historical forecast would have been issued. The [database history](persistence.md) captures changes observed after collection begins; it cannot reconstruct earlier provider publication times. If historical availability cannot be established, disclose that the evaluation uses retrospectively available data rather than claiming a point-in-time operational backtest.

Report forecast error with defined metrics, not an unexplained percentage called "accuracy." MAE, RMSE, and suitable baseline-relative measures are candidates; final choices depend on the target and units. Percentage metrics require care around zero or near-zero values.

Report results by forecast horizon and season, alongside overall summaries and uncertainty estimates. Include failed approaches and cases where baselines win.

### Revision-Aware Evaluation

The [October 6-13 collection evidence](statnett-source-investigation.md#observed-api-revisions) records changes to 22 distinct completed hourly observations, including five detected on October 13. This makes revision risk an observed property of our captures, not merely a hypothetical concern. The audit was selective; no population revision rate or forecasting effect has been established.

Before claiming point-in-time operational evaluation, implement and test selection of observation versions captured by each forecast issue time. Do not substitute current values for versions unavailable then. Scheduled collection and documented refresh coverage are needed; even after collection starts, unobserved intermediate versions and unknown provider publication times remain limitations.

Compare capture-available inputs with retrospectively revised inputs using the same forecast timestamps, feature definitions, splits, models, and fixed evaluation-reference snapshot. Report changes in features, predictions, errors, and prediction coverage rather than assuming revisions necessarily worsen or improve performance. Assess evaluation-label revisions separately by rescoring fixed predictions against explicitly identified reference versions.

Revision statistics should use the overlapping hours actually compared as their denominator and distinguish changes by metric, magnitude, and observation age at detection. Preserve dated ledger/run exports, linked raw archives, and checksums for the published audit. Run counts alone do not measure revision frequency, and historical availability before the first capture remains unrecoverable from this collection.

## Publication and Reproducibility

### Experiment Records

Experiment 002 now owns its notebook controller, feature settings, benchmark settings, and reproduction notes. The redundant 001 feature-study source directory was removed; historical configs remain in Git and generated data/runs were preserved. Each execution snapshots source configs/notebook, creates a new audit/features/benchmark chain, and records explicit resolved bindings without rewriting source settings. Changed hypotheses or settings require a new experiment version. A frozen notebook alone does not freeze its dependencies or imported code; stage manifests retain software and implementation hashes.

The notebook calls reusable package APIs and runs top-to-bottom in a clean kernel. `python -m oslo_energy.pipeline.run_experiment --experiment-dir experiments/002_baselines_linear_validation_v1` selects the current project interpreter and saves an executed notebook plus a completion checksum record alongside stage outputs. The source notebook keeps empty outputs. Full experiments are explicitly invoked; automated tests include a tiny synthetic clean-kernel notebook check, not a real-data result. Notebook execution does not collect live data, delete old artifacts, select mutable latest snapshots, or score the final test.

New experiments can be scaffolded with `python -m oslo_energy.pipeline.create_experiment <new_id>`. The generator creates a README, feature config, benchmark config, and generic notebook rather than copying historical results or hardcoding local dataset references. Missing inputs/dates are explicit placeholders; optional cleaned-run and window arguments verify/pin known inputs. Generated sources need no initial audit/feature hashes: the controller binds its new stages in resolved configs, while standalone stage commands retain strict pinned-input requirements. Generation never executes research or overwrites an existing experiment.

Track the lineage from normalized input checksums to the cleaning run, feature recipe/run, experiment configuration/execution, and each fold's fitted preprocessing and model state. Record ordered treatment versions/configuration, feature families and definitions, target/horizons, split boundaries, model parameters, tuning budget, seeds, code/software versions, and evaluation-reference identity. Preserve timestamped predictions and metrics so comparisons can be recomputed.

Unique execution IDs identify individual attempts. Deterministic configuration fingerprints identify settings, while data/artifact checksums identify actual inputs and outputs; equal settings alone do not imply equal datasets or results. Each downstream record references its parents when it is created. A cleaning manifest need not contain IDs for feature or model executions that have not happened yet.

Each published experiment should identify:

- The dataset snapshot, checksums, coverage, units, exclusions, and unresolved source limitations.
- The target, horizons, temporal splits, and information available at forecast time.
- Feature definitions, preprocessing, model configurations, tuning procedure, and random seeds where relevant.
- Results for baselines, statistical models, and ML models on the same evaluation periods.
- Scripts or instructions to reproduce the experiment, with software versions and computational requirements.

We intend to publish findings and model performance, including negative results. When the research is complete, we will publish the versioned dataset used in the reported experiments to support reproducibility, subject to the data providers' redistribution terms. The dataset release will include checksums, provenance, observed revision history and capture timestamps, and the documented transformations and exclusions needed to identify the exact research inputs and evaluation reference. Data and model-artifact distribution will need to respect Statnett's terms and any additional source licenses; the project's MIT license does not grant rights to third-party data.

## Contributing to the Research

Useful contributions now include source-validation evidence, missing-data analysis, tests, and clearer documentation. Proposed feature or modeling work should state the research question, required data, forecast-time availability, and evaluation method before implementation.

Return to the [project overview](../README.md) for setup and the current status.
