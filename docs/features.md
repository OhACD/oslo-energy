# Basic Feature Pipeline

The initial feature pipeline builds stateless, versioned consumption-lag and calendar features from a pinned cleaned run and matching research audit. It does not train a model, fit preprocessing, modify observations, or certify operational forecast availability. Source limitations and `training_ready: false` remain explicit.

## Forecast And Availability Contract

For target start `t`, the target is consumption during `[t, t+1h)` and the forecast is issued at `t`. The source hour for an elapsed lag `k` starts at `t-k hours`. A source measurement is usable only when:

```text
source_period_end_utc + publication_delay_hours <= forecast_issue_time_utc
```

Only strictly positive integer lags are accepted. Target-hour consumption is never a feature. `CausalHistory` exposes time-checked lag lookups rather than an unrestricted observation table to family implementations; unavailable values are masked before returning. The shared validator rejects valid measurement features whose declared availability exceeds issue time. Known calendar facts need no observed measurement and carry no measurement-availability timestamp.

This is a sequential one-hour forecast task, not one forecast of an entire test year at a fixed origin. A later validation/test forecast can use consumption from earlier elapsed validation/test hours under the configured availability assumption. Training and validation are not sources of fitted state in this pipeline: no fitting occurs. Future scalers, imputers, feature selection, and model fitting must use training examples only; model checkpoint selection may use validation, never test outcomes.

**Event-time checks do not prove historical provider availability.** A zero delay means assuming immediate publication after an hour completes. Statnett's true publication delay is unverified, and retrospectively revised history may differ from what existed then. The configuration records this assumption. A positive delay masks affected inputs, rather than silently shifting lag definitions or filling values. For example, a one-hour publication delay makes a lag of one hour unavailable; select an explicitly different lag recipe if that is the intended experiment. No point-in-time operational claim is justified until actual capture/availability evidence supports it.

## Families And Recipe

| Family | Version | Settings | Output |
| --- | --- | --- | --- |
| `consumption_lags` | `1` | Distinct positive integer `lags`, default `[1, 24, 168]` | `consumption_lag_1h`, `consumption_lag_24h`, `consumption_lag_168h` for the initial recipe |
| `calendar` | `1` | None | `oslo_hour_sin`, `oslo_hour_cos`, `oslo_dayofweek_sin`, `oslo_dayofweek_cos` |

Calendar sine/cosine encode `2*pi*hour/24` and `2*pi*dayofweek/7` from the target's `Europe/Oslo` calendar, with Monday zero. Repeated autumn clock hours can share calendar encodings while remaining separate UTC identities. Lags always measure elapsed UTC hours, not same local clock hour across DST.

Sparse lag features require their exact source hours, not a complete intervening week. Missing rows do not compress elapsed time. Earlier history outside the configured target windows is usable if causal; no automatic 168-hour truncation is applied to each split. Null production does not affect these consumption-only features. Missing inputs/targets are never filled; suspicious values are retained rather than silently corrected.

## Shared Configuration And Commands

Experiment 002's [feature configuration](../experiments/002_baselines_linear_validation_v1/feature_config.json) is shared by audit and feature commands and its notebook controller. It specifies input manifest hashes, target/availability semantics, chronological train/validation/test windows, recipe identity, family versions/settings, and preservation rules. Input paths resolve relative to the config file, not the shell working directory. Windows use inclusive Oslo dates and assign examples by target start timestamp. The earlier 001 source directory was consolidated into 002, without removing historical generated artifacts.

From the repository root:

```bash
python -m oslo_energy.pipeline.run_research_audit --config experiments/002_baselines_linear_validation_v1/feature_config.json
python -m oslo_energy.pipeline.run_features --config experiments/002_baselines_linear_validation_v1/feature_config.json
```

The audit still accepts its original `--input-dir` and repeated `--window` form. Do not mix that form with `--config`. A feature run requires a matching audit whose windows and cleaning-manifest identity agree with the configuration. Editing dates without generating and referencing matching audit evidence fails. Audit references do not override `pending_review` eligibility. No arbitrary module imports or unregistered families are accepted. Unknown configuration keys, duplicate families/columns, unsupported horizons, invalid lag settings, and nonchronological/overlapping splits fail explicitly.

The default feature output root is `data/features/statnett`; `--output-dir` overrides it. New runs never overwrite previous executions or source/configuration directories. Exit `0` means a completed feature execution with source warnings, not training approval. Processing failures return `1`, argument errors use argparse's `2`.

The notebook controller regenerates the audit first and writes a resolved feature config under its new execution, explicitly binding that audit. It generates features under the same execution and writes a separate resolved benchmark config that pins their manifest. This avoids editing source settings or relying on mutable latest-run lookup. Source configs remain authoritative for the recipe/model choices; resolved configs record stage identities. Feature outputs inside a source experiment are allowed only under its designated `runs/` subtree.

New [generated blueprints](../README.md#create-a-new-experiment) can omit an initial audit reference because the controller produces it. Only the controller's initial source loading permits that omission; standalone `run_features` still requires an identified compatible audit. The generator fills known family/model defaults and leaves unknown input hashes/dates as visible placeholders that must be completed before execution.

## Artifacts And Safe Model Inputs

| Artifact | Purpose |
| --- | --- |
| `features.csv` | UTC target key plus declared numeric feature columns only; no targets, split labels, retrospective diagnostic flags, or eligibility masks |
| `examples.csv` | Issue/target times, split, retrospective target values/availability, source-row presence, input availability, and explicit eligibility masks |
| `feature_quality.csv` | Per-feature validity, missingness reason, and availability time |
| `feature_report.json` | Effective configuration, family metadata, per-split counts, source limitations, and checks |
| `manifest.json` | Completion marker, parent/config/output checksums, configuration fingerprint, implementation hashes, and software versions |

Every intended target timestamp is retained. `target_available` means an observed retrospective reference exists, not that the target was known at issue time. `features_available` depends only on declared feature values. `complete_example` requires both, while `training_example_eligible` additionally requires the training split. `scoring_reference_available` says only whether a reference label exists, not that a model produced a prediction. Missing source rows have no fabricated source identity.

Missingness reasons include `insufficient_history`, `absent_source_hour`, `missing_measurement`, and `not_available_at_issue`. No missingness or quality flag is included as a predictive feature. Keeping all rows prevents unavailable cases from silently redefining the evaluation task.

Use `load_feature_matrix(run_dir, split="train")` from [the feature runner](../src/oslo_energy/pipeline/features.py) for downstream X inputs. It verifies artifact hashes and registered recipe columns and returns only numeric features, with UTC keys as the index. It retains missing inputs so downstream eligibility remains explicit. Do not feed the examples table, audit statistics, target availability flags, or all numeric columns from arbitrary CSVs to a model. Labels are loaded separately for the appropriate training/scoring role.

Publication rechecks parent artifacts/configuration for mutation and writes the manifest last. Failed runs remove only their new directory. `validate_feature_run` verifies completion and output integrity. Fingerprints describe effective settings, not identical data; unique execution IDs and input/output hashes remain separate. Hashes are integrity checks, not signatures against deliberate forgery.

## Extending And Testing

The [feature package](../src/oslo_energy/transformation/features/) separates contracts, base execution, validation, registry, and concrete family modules. To add a family, subclass `FeatureFamily`, declare ID/version/settings/schema/input requirements, implement `_build`, test availability and temporal invariants, and register the tested class in [the allowlist](../src/oslo_energy/transformation/features/registry.py). Callers and CLI do not need family-specific branches. Import internal types from their owning modules to avoid cycles.

The history API and validator reduce accidental leakage; they are not a sandbox or a universal mathematical proof of arbitrary future family code. New families need their own adversarial tests. Retrospective anomaly flags and learned statistics must not enter predictors without a separate forecast-availability/fold-fitting design.

Focused tests:

```bash
python -m pytest -q tests/transformation/test_feature_families.py tests/pipeline/test_research_config.py tests/pipeline/test_features.py
```

Tests perturb target and future measurements at multiple cutoffs and require earlier features to remain unchanged; compare full-history and truncated-history results; reject lag zero/negative settings, delayed-input claims, and naive/misaligned timestamps; verify DST, missing rows, pre-window history, null propagation, and separate label/input eligibility. Integration tests cover shared config/audit agreement, schema allowlists, mutation, checksums, repeatability, and failed writes. Future model/preprocessing tests must separately prove training-only fitting, temporal tuning, and fixed-reference scoring before any accuracy claim.
