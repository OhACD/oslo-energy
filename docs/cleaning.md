# Cleaning Pipeline Design

**Owner:** [Alsir Taha](https://github.com/kabakat27).

**Status: initial implementation available.** The standalone hourly cleaning runner implements input validation, diagnostics, the policy contract, and `preserve_and_flag`. Additional policies and downstream feature/model contracts remain planned. Cleaning does not change ingestion, normalized outputs, PostgreSQL records, or the meaning of `training_ready: false`.

The cleaning stage creates a traceable dataset for research and later feature engineering. It does not establish that Statnett measurements are accurate, final, or suitable for forecasting. See the [Statnett investigation](statnett-source-investigation.md) for unresolved source questions and the [research plan](research-plan.md) for the intended evaluation approach.

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

The existing normalizer remains responsible for source validation, UTC period mapping, candidate partitioning, and `quality.json`. Cleaning consumes completed normalization artifacts; it does not call Statnett or replace the normalizer's report. It is a separately invoked command, never an automatic ingestion step.

## Input Contract

The initial input is one normalized run directory containing:

| Artifact | Requirement | Use |
| --- | --- | --- |
| `historical_candidates.csv` | Required | Candidate observations for the selected cleaning policy |
| `quality.json` | Required | Normalization provenance, validation context, and available source-quality/coverage findings |
| `incomplete.csv` | Optional | Diagnostic context only; its rows are never added to the historical analytical dataset |

`future_quarantine.csv` is not an input to the initial cleaning scope. The input adapter should validate required files, report compatibility, and schema before diagnostics or policy execution. Missing or contradictory required metadata is a failure, not a reason to guess.

The normalized hourly observation identity is `period_start_utc`. The existing fields are `period_start_utc`, `period_end_utc`, `observation_date`, `period_hours`, `source_index`, `production`, and `consumption`. Preserve UTC identity for ordering and uniqueness; `observation_date` is the `Europe/Oslo` local date used for grouping, not a unique key. A local day may contain 23, 24, or 25 hourly observations.

The implementation accepts hourly normalized runs only. The normalizer can also replay a legacy daily format, but its date identity and DST-padding contract differ; daily cleaning requires an explicit schema and dedicated tests.

The adapter requires the current normalizer's report fields and exact observation column set. It checks partition counts, missingness metadata, source dates, completed-hour boundaries, local dates, and ordered unique UTC/source identities. Only blank measurement cells mean missing: malformed numbers, non-finite values, and negatives fail. Optional incomplete rows are validated but never selected for output. Whole-run null counts are checked only to the extent established by available partitions; missing optional context does not imply complete coverage. Empty historical partitions are supported.

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

Implemented diagnostics are metric-specific `missing_measurement`, `absent_hour`, `source_units_unvalidated`, and `source_reference_unresolved`. Internal gaps use the observed UTC span; endpoint gaps additionally require validated `request_coverage`. Statistical behavior and independent source accuracy are `not_run`. Check statuses distinguish `passed`, `findings`, and `not_run`; contract failures stop execution rather than publishing a successful report.

## Policy Contract

Each reusable cleaning policy implements the shared [base contract](../src/oslo_energy/transformation/cleaning/base.py), using the shared [context and result types](../src/oslo_energy/transformation/cleaning/contracts.py). The contract provides:

- A stable policy identifier and explicit version.
- Validated, serializable effective configuration.
- A common context containing normalized observations, normalization metadata, diagnostic findings, dataset identity, and execution/provenance metadata.
- A structured result containing analytical observations, quality findings, action records, counts, warnings, and unresolved conditions.
- Explicit declarations of whether it may exclude rows, change values, or introduce synthetic values.
- Preserved observation identity and provenance, with deterministic behavior for a fixed input and configuration. Policies using randomness must record a seed.

`CleaningPolicy.apply(CleaningContext) -> CleaningResult` isolates source inputs and validates capabilities, identities, original measurements, diagnostic preservation, and explicit linked actions. Subclasses implement `_apply`, declare `policy_id`, `version`, and `PolicyCapabilities`, and override `validate_configuration` when they have settings. The base class contains no imputation, exclusion, or corruption logic. `POLICY_REGISTRY` maps stable policy IDs to known policy classes; unknown IDs are rejected and arbitrary imports are never used.

To add a policy:

1. Add a module under [policies](../src/oslo_energy/transformation/cleaning/policies/), importing `CleaningPolicy` from `cleaning.base` and shared types from `cleaning.contracts`. Internal modules import their owning modules directly rather than the package facade to avoid circular imports.
2. Subclass `CleaningPolicy`, declare its stable ID/version/capabilities, implement `_apply`, and validate any settings through `validate_configuration`. Leave `apply` inherited so input isolation and shared validation still run.
3. Add policy-specific tests for decisions, configuration, originals, findings, and linked actions. Shared contract checks enforce invariants; they do not establish that a policy's scientific decisions are appropriate.
4. Import the tested class in [registry.py](../src/oslo_energy/transformation/cleaning/registry.py) and add its ID-to-class entry to `POLICY_REGISTRY`. The runner and CLI require no policy-specific changes. Ordinary callers can keep importing from `oslo_energy.transformation.cleaning` through the stable package exports.

The initial policy accepts no configuration settings. Configuration must serialize as finite JSON; effective settings are copied into the report and fingerprinted. Diagnostic findings and policy actions use `QualityRecord`, while result counts are computed from observations and actions rather than supplied estimates.

This contract describes standalone cleaning runs, not all downstream data treatments. Learned preprocessing requires a separate fold-scoped fit/transform contract; experiment recipes select and combine treatments without forcing them into one cleaning-policy interface. Only completed, tested cleaning policies should be exposed by the cleaning CLI.

### Initial Policy

`preserve_and_flag` is the first implemented policy. It retains all input observations and original measurements, adds diagnostic findings and explicit policy outcomes, and makes no imputation, scaling, correction, or automatic exclusion. It provides a useful audited baseline while leaving downstream users responsible for selecting observations suitable for a particular experiment.

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

The default root is `data/cleaned/statnett`; run IDs combine the policy ID and a UUID. Neither the normalized input run nor another cleaning run is overwritten. Outputs inside the normalized input directory are rejected.

| Artifact | Contents |
| --- | --- |
| `observations.csv` | Analytical observations and stable source identity/provenance fields |
| `row_quality.csv` | Findings and policy actions linked to an observation or an absent interval; a finding for a missing hour must not require a fabricated observation row |
| `cleaning_report.json` | Run status, input reference, policy/configuration, counts, findings by code/severity, outcomes, warnings, and unresolved limitations |
| `manifest.json` | Run identity, input/output checksums, policy/version/configuration, seed, code version when available, and execution timestamp |

For a stable analytical schema, retain canonical `production` and `consumption` columns as the values downstream consumers use, and include `production_original` and `consumption_original` for traceability. Under `preserve_and_flag`, each analytical value equals its original value. If a policy transforms a value, the original remains unchanged. Identity fields retain their normalized meanings. Policy identity and run-level configuration belong in the report and manifest; row-level actions belong in `row_quality.csv`.

Use stable diagnostic codes and structured action fields rather than relying on free-form text alone. Counts must be computed from the run. A check that was not performed is `not_run`, not `passed`. Hash input and output artifacts when available; the manifest must not claim to checksum itself. The normalized report may identify an archive path without containing its checksum. Compute that checksum only if the referenced archive is accessible; otherwise record it as unavailable.

Output/report schema version is `1`, separate from policy version `1`. `row_quality.csv` has `kind`, `code`, `severity`, UTC interval boundaries, `metric`, `action`, and `detail`. Missing-hour records have no invented source row. `preserve_and_flag` emits an `observation_retained`/`retain` action per source observation. Canonical values and their `_original` columns remain equal, including nulls.

`manifest.json` is the completion marker, written last. It records consumed input checksums, the three other output checksums, raw archive checksum availability, execution time, policy configuration/capabilities, configuration fingerprint, and Git commit when available plus package-source hashes. Its seed is null for this deterministic policy. A failed write or validation removes only the newly created execution directory. Input hashes are rechecked before completion; source changes during processing fail the run. Use `validate_cleaning_run(Path(...))` from [pipeline/cleaning.py](../src/oslo_energy/pipeline/cleaning.py) to require the completion marker and verify output integrity. Hashes detect accidental changes; they are not signed authenticity guarantees.

Distinguish a unique execution ID from a deterministic configuration fingerprint and input/output artifact checksums. A matching configuration fingerprint alone does not establish identical data or results. Downstream feature and experiment records should reference the cleaning run and its artifact checksums, then record their own recipe versions and executions. Fold-specific fitted preprocessing and model state belong to those downstream records, not the cleaning manifest. No future feature or model run ID is required before those runs exist.

The report should distinguish:

- `success`: required processing and validation completed.
- `completed_with_warnings`: output was produced, but findings or source limitations remain.
- `failed`: a required contract or processing step could not complete reliably.

These are operational outcomes. None sets `training_ready` to true. A dataset's research eligibility requires a separate, task-specific assessment.

## CLI And Failure Behavior

Run from the repository root against an existing hourly normalization run:

```bash
python -m oslo_energy.pipeline.run_cleaning \
  --input-dir data/normalized/statnett/<normalization_run_id> \
  --policy preserve_and_flag \
  --output-dir data/cleaned/statnett
```

`--input-dir` is required; policy and output root default to the values above. Relative paths resolve from the working directory. `--help` lists registered usable policies. The runner validates inputs, computes diagnostics, applies the selected policy, validates its output, and writes a new run. Exit code `0` includes completed outputs with source warnings; code `1` reports processing failure, and invalid arguments use argparse's code `2`. Existing source limitations mean initial runs normally have status `completed_with_warnings`. Rerunning the same input needs neither an API request nor a database connection and creates a distinct execution. Observations/findings and configuration fingerprints are deterministic; timestamps and execution IDs intentionally differ.

Implementation boundaries:

| Component | Responsibility |
| --- | --- |
| [Public exports](../src/oslo_energy/transformation/cleaning/__init__.py) | Preserve the `oslo_energy.transformation.cleaning` import API |
| [Contracts](../src/oslo_energy/transformation/cleaning/contracts.py) | Observation schema, context/result/record types, and capability declarations |
| [Base policy](../src/oslo_energy/transformation/cleaning/base.py) | Configuration validation and isolated policy execution |
| [Result validation](../src/oslo_energy/transformation/cleaning/validation.py) | Enforce shared identity, provenance, capability, and action invariants |
| [Registry](../src/oslo_energy/transformation/cleaning/registry.py) | Explicit policy allowlist and resolution |
| [Preserve and flag](../src/oslo_energy/transformation/cleaning/policies/preserve_and_flag.py) | Concrete non-destructive policy decisions |
| [Cleaning runner](../src/oslo_energy/pipeline/cleaning.py) | Input validation, independent diagnostics, execution, reports, checksums, and completion verification |
| [Cleaning CLI](../src/oslo_energy/pipeline/run_cleaning.py) | Argument parsing, registry selection, status output, and exit codes |

Run the focused offline checks:

```bash
python -m pytest -q tests/transformation/test_cleaning_policy.py tests/pipeline/test_cleaning.py
```

## Handoff To Feature Engineering And Research

Cleaning produces a versioned analytical observation dataset, not a feature matrix or model-ready split. Feature engineering should consume a specific cleaned run and record its own definitions, configuration, and lineage. Keep feature-set comparisons separate from cleaning-policy comparisons so an experiment can attribute outcome changes to one choice at a time.

Before real-data feature selection, run the separate [research-readiness audit](research-readiness.md) over explicit candidate experiment windows. It verifies cleaning artifacts and describes endpoint coverage, missing stretches, distributions, extremes, and source context without modifying cleaning reports or observations. Its descriptive checks do not retrospectively turn cleaning `not_run` checks into `passed`, and eligibility remains pending explicit review.

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
