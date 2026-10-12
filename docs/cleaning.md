# Cleaning Pipeline Design

**Owner:** [Alsir Taha](https://github.com/kabakat27).

**Status: proposed; not implemented.** This document defines a separate analytical cleaning stage after normalization. It does not change ingestion, normalized outputs, PostgreSQL records, or the meaning of `training_ready: false`.

The cleaning stage will create a traceable dataset for research and later feature engineering. It will not establish that Statnett measurements are accurate, final, or suitable for forecasting. See the [Statnett investigation](statnett-source-investigation.md) for unresolved source questions and the [research plan](research-plan.md) for the intended evaluation approach.

## Design Goals

- Preserve raw archives, normalized files, and database revision history.
- Keep diagnosis separate from policy decisions: diagnostics describe findings; policies decide whether to retain, exclude, or transform observations.
- Make every derived dataset reproducible from an identified normalized run, policy version, effective configuration, and available input checksums.
- Make transformations explicit and preserve source values when analytical values change.
- Keep cleaning execution status separate from scientific or forecasting eligibility.
- Keep feature engineering and model-specific preprocessing downstream of cleaning.

## Pipeline Boundary

```text
Statnett API -> immutable raw archive -> existing normalization -> normalized run
                                                               |
                                                     standalone cleaning CLI
                                                               |
                              diagnostics -> policy -> validation and reporting
                                                               |
                              derived observations + quality + manifest
                                                               |
                                       research and feature engineering
```

The existing normalizer remains responsible for source validation, UTC period mapping, candidate partitioning, and `quality.json`. Cleaning consumes completed normalization artifacts; it does not call Statnett or replace the normalizer's report. The first version should be a separately invoked command, never an automatic ingestion step.

## Input Contract

The initial input is one normalized run directory containing:

| Artifact | Requirement | Use |
| --- | --- | --- |
| `historical_candidates.csv` | Required | Candidate observations for the selected cleaning policy |
| `quality.json` | Required | Normalization provenance, validation context, and available source-quality/coverage findings |
| `incomplete.csv` | Optional | Diagnostic context only; its rows are never added to the historical analytical dataset |

`future_quarantine.csv` is not an input to the initial cleaning scope. The input adapter should validate required files, report compatibility, and schema before diagnostics or policy execution. Missing or contradictory required metadata is a failure, not a reason to guess.

The normalized hourly observation identity is `period_start_utc`. The existing fields are `period_start_utc`, `period_end_utc`, `observation_date`, `period_hours`, `source_index`, `production`, and `consumption`. Preserve UTC identity for ordering and uniqueness; `observation_date` is the `Europe/Oslo` local date used for grouping, not a unique key. A local day may contain 23, 24, or 25 hourly observations.

The initial implementation should accept hourly normalized runs only. The normalizer can also replay a legacy daily format, but its date identity and DST-padding contract differ; daily cleaning should be added only with an explicit schema and dedicated tests.

`quality.json` may contain `request_coverage` when the original request bounds are known. If it is absent, requested-range coverage was not inferred; absence is not evidence that coverage is complete. Carry source findings and limitations into the cleaning report rather than letting a policy make them disappear. Normalized values remain in provider units, which are not yet independently validated.

## Diagnostics And Decisions

Diagnostics produce structured findings without changing measurements or excluding rows. Policies consume those findings and make explicit decisions. The pipeline orchestrates input loading, validation, diagnostics, policy execution, output validation, and reporting; the policy base contract must not implement every policy's behavior.

| Area | Check or finding | Handling |
| --- | --- | --- |
| Input contract | Required artifacts, columns, parseable types, period boundaries, and provenance | Fail when the required contract cannot be established |
| Observation identity | Duplicate or conflicting UTC period identities | Fail until collisions are resolved; do not choose a row silently |
| Time-grid continuity | Expected UTC hours absent from the input | Report as interval/dataset findings; an absent hour has no source row to flag |
| Measurement completeness | Existing row has null `production` or `consumption` | Flag by metric and preserve unless a selected policy says otherwise |
| Measurement domain | Non-finite or negative values | Reuse the normalizer's source contract; do not invent a competing validity rule |
| Statistical behavior | Large changes or unusual distributions | Flag for investigation; do not automatically delete, cap, or correct |
| Source uncertainty | Known API/export disagreement, units, or finality questions | Carry forward as warnings and eligibility restrictions |

Missing rows and missing measurements are distinct. Compare against a UTC grid only where a defensible expected range is available, such as request coverage recorded in the normalization report. Do not assume every unbounded or replayed run has known endpoint coverage. A 23- or 25-hour local day is not itself a gap.

Hard input-contract failures, diagnostic findings, and policy outcomes must remain separately identifiable. Thresholds for statistical findings are versioned configuration with documented rationale; no universal consumption threshold is assumed here.

## Policy Contract

Each reusable cleaning policy should implement one small, shared contract. Exact Python types and signatures should be chosen alongside implementation and tests, but the contract must provide:

- A stable policy identifier and explicit version.
- Validated, serializable effective configuration.
- A common context containing normalized observations, normalization metadata, diagnostic findings, dataset identity, and execution/provenance metadata.
- A structured result containing analytical observations, quality findings, action records, counts, warnings, and unresolved conditions.
- Explicit declarations of whether it may exclude rows, change values, or introduce synthetic values.
- Preserved observation identity and provenance, with deterministic behavior for a fixed input and configuration. Policies using randomness must record a seed.

The base class should enforce the shared contract, not contain imputation, exclusion, or corruption logic. A registry should map stable policy IDs to known policy classes; reject unknown IDs and never import arbitrary modules from user-provided names.

This contract describes standalone cleaning runs, not all downstream data treatments. Learned preprocessing requires a separate fold-scoped fit/transform contract; experiment recipes select and combine treatments without forcing them into one cleaning-policy interface. Only completed, tested cleaning policies should be exposed by the cleaning CLI.

### Initial Policy

`preserve_and_flag` is the first policy to implement. It retains all input observations and original measurements, adds diagnostic findings and explicit policy outcomes, and makes no imputation, scaling, correction, or automatic exclusion. It provides a useful audited baseline while leaving downstream users responsible for selecting observations suitable for a particular experiment.

Its observations and quality findings form a reusable baseline for multiple feature recipes and model experiments. Preserving a missing value is an explicit decision, not a promise that every model can accept it.

### Deferred Research Treatments

The remaining strategy names describe useful research treatments, but they do not all produce reusable, globally cleaned tables. Their execution scope depends on the task and method; none is part of the initial usable implementation:

| Policy | Purpose | Design constraint |
| --- | --- | --- |
| `complete_cases` | Exclude examples missing explicitly required fields; never fill values | Assess feature/target eligibility after constructing task examples. Observation-only exclusions may produce a separately identified derived dataset, but must preserve timestamp gaps. Do not require unrelated optional metrics. |
| `imputation` | Estimate and mark replacements for selected missing inputs | Learned statistics belong inside each training fold, not one globally imputed cleaning output. Fixed causal methods also need forecast-time availability and gap limits. Record originals, method, and configuration; future-assisted interpolation is not automatically valid for forecasting. |
| `controlled_degradation` | Deliberately mask measurements or add configured noise for robustness tests | Require opt-in, a recorded seed, affected fields/partitions, and synthetic-change records in a separate experiment branch. Leave evaluation labels unchanged by default; never overwrite a reference dataset. |

These are different analytical experiments, not progressively stronger stages to chain together by default. Missing target labels should be excluded from fitting and scoring under a documented shared rule, not silently imputed as observed truth. Missing input features may instead be treated according to model requirements.

Training-data corruption and corruption of inputs available at forecast time answer different robustness questions. State which condition is being tested and which partitions it affects. Combined treatments require an explicit ordered recipe with stable strategy IDs, versions, configuration, and seeds; they are never an implicit default chain.

## Outputs And Lineage

Each cleaning execution writes to a new run directory under a separate cleaned-data root, for example:

```text
data/cleaned/statnett/<cleaning_run_id>/
    observations.csv
    row_quality.csv
    cleaning_report.json
    manifest.json
```

The exact root and run-ID scheme should follow repository conventions when implemented. Never overwrite the normalized input run or another cleaning run.

| Artifact | Proposed contents |
| --- | --- |
| `observations.csv` | Analytical observations and stable source identity/provenance fields |
| `row_quality.csv` | Findings and policy actions linked to an observation or an absent interval; a finding for a missing hour must not require a fabricated observation row |
| `cleaning_report.json` | Run status, input reference, policy/configuration, counts, findings by code/severity, outcomes, warnings, and unresolved limitations |
| `manifest.json` | Run identity, input/output checksums, policy/version/configuration, seed, code version when available, and execution timestamp |

For a stable analytical schema, retain canonical `production` and `consumption` columns as the values downstream consumers use, and include `production_original` and `consumption_original` for traceability. Under `preserve_and_flag`, each analytical value equals its original value. If a policy transforms a value, the original remains unchanged. Identity fields retain their normalized meanings. Policy identity and run-level configuration belong in the report and manifest; row-level actions belong in `row_quality.csv`.

Use stable diagnostic codes and structured action fields rather than relying on free-form text alone. Counts must be computed from the run. A check that was not performed is `not_run`, not `passed`. Hash input and output artifacts when available; the manifest must not claim to checksum itself. The normalized report may identify an archive path without containing its checksum. Compute that checksum only if the referenced archive is accessible; otherwise record it as unavailable.

Distinguish a unique execution ID from a deterministic configuration fingerprint and input/output artifact checksums. A matching configuration fingerprint alone does not establish identical data or results. Downstream feature and experiment records should reference the cleaning run and its artifact checksums, then record their own recipe versions and executions. Fold-specific fitted preprocessing and model state belong to those downstream records, not the cleaning manifest. No future feature or model run ID is required before those runs exist.

The report should distinguish:

- `success`: required processing and validation completed.
- `completed_with_warnings`: output was produced, but findings or source limitations remain.
- `failed`: a required contract or processing step could not complete reliably.

These are operational outcomes. None sets `training_ready` to true. A dataset's research eligibility requires a separate, task-specific assessment.

## CLI And Failure Behavior

The proposed command is illustrative; no cleaning CLI currently exists:

```bash
python -m oslo_energy.pipeline.run_cleaning \
  --input-dir data/normalized/statnett/<normalization_run_id> \
  --policy preserve_and_flag
```

The runner should resolve and validate the input run, load the report and observations, compute diagnostics, select and validate a registered policy, validate its output, and write a new cleaned run with its report and manifest. It should return a nonzero exit code on failure. Partial artifacts must not be mistaken for a completed run. Cleaning should be rerunnable against the same normalized input without an API request or database connection.

## Handoff To Feature Engineering And Research

Cleaning produces a versioned analytical observation dataset, not a feature matrix or model-ready split. Feature engineering should consume a specific cleaned run and record its own definitions, configuration, and lineage. Keep feature-set comparisons separate from cleaning-policy comparisons so an experiment can attribute outcome changes to one choice at a time.

For strategy comparisons, use the same versioned feature definitions and settings, not necessarily identical feature values or identical training-example counts. Calendar features may remain unchanged while consumption lags and rolling summaries change with the treatment. Feature families and their combinations are downstream experiment choices, not cleaning responsibilities.

Feature recipes must define how absent hours and null values propagate into lags and rolling summaries, how much valid history a window requires, and how the selected model handles missing inputs. Never substitute zero or silently calculate a supposedly complete statistic from partial history. Removing an observation must not make the next available row stand for the next UTC hour; use timestamp-aware windows or an explicitly represented UTC grid. Derived placeholder hours, if needed, must be marked as absent source observations rather than assigned invented source provenance.

Treatment order is strategy-specific and recorded explicitly. Observation-level imputation may precede lag generation within a fold; feature-level imputation may follow feature construction. In either case, learned state is fitted only from training history and transformations use only inputs available at the forecast issue time. There is no universal feature-build-then-impute ordering, and reusable cleaning runs do not pre-fit these treatments on the whole dataset.

For forecasting experiments:

- Split observations chronologically before fitting imputers, scalers, feature selectors, or other learned preprocessing; fit them only on each training fold.
- Calculate lags and rolling summaries using only observations available at forecast time. Future observed weather or same-hour production is not automatically a valid forecast input.
- Retrospective diagnostic flags are not automatically forecast-available features. Thresholds or statistics used for predictive decisions must be fixed independently of held-out data or fitted within the training fold.
- Preserve UTC ordering and identity across daylight-saving transitions; use local dates for calendar features and grouping, not as a substitute for UTC identity.
- Treat revisions as a point-in-time limitation. Current database values and revision history do not reconstruct what Statnett had published at every historical forecast time.
- Keep the held-out evaluation reference fixed when comparing missingness, imputation, noise, or other data-quality conditions, and disclose when independent ground truth is unavailable.
- Keep intended forecast timestamps fixed and report prediction coverage and failures alongside error metrics; do not reward a strategy merely for skipping difficult examples.

The [research plan](research-plan.md) defines the broader experimental principles. Cleaning success alone never establishes forecast-time availability, source accuracy, or training eligibility.

## Tests And Acceptance Criteria

Tests should run offline against synthetic data. At minimum, verify:

- Input schema, missing artifacts, incompatible metadata, duplicate UTC identities, null measurements, and missing-hour findings.
- DST-aware 23- and 25-hour local days without false gap findings.
- `preserve_and_flag` leaves values and observation identity unchanged while recording findings.
- Policy configuration and registry rejection behavior; policy-specific tests before a policy is exposed as usable.
- Output schema, action/findings linkage, actual counts, report status, manifest checksums, and deterministic reruns.
- Clear separation of reusable outputs and fold-fitted treatments, with no training/test leakage or silent target-label filling.
- Timestamp-aware features and eligibility rules that preserve gaps and enforce explicit null-window coverage requirements.
- Reproducible ordered treatment recipes and seeded degradation, with unchanged evaluation labels and reported prediction coverage.
- Linked input, cleaning, feature, experiment, and fold artifacts with separate execution IDs, configuration fingerprints, and data checksums.
- Source archives, normalized CSVs, normalization reports, and PostgreSQL history are not modified.
- Cleaning status is never represented as `training_ready: true`.

Existing normalization and persistence tests remain responsible for their current contracts; cleaning tests should not require Statnett access or PostgreSQL. Fold, feature, and experiment checks belong to their respective future test suites rather than expanding the cleaning runner into a training system.
