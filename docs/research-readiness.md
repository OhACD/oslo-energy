# Research-Readiness Audit

The standalone audit checks a completed cleaning run against **explicit research windows** before feature selection or model training. It is descriptive and non-mutating: it does not repair measurements, reconstruct original request bounds, establish independent source accuracy, or approve training. This is separate from the [cleaning policy contract](cleaning.md).

## Run An Audit

From the repository root, using the existing environment:

```bash
python -m oslo_energy.pipeline.run_research_audit \
  --input-dir data/cleaned/statnett/<cleaning_run_id> \
  --window train:2018-01-01:2023-12-31 \
  --window validation:2024-01-01:2024-12-31 \
  --window test:2025-01-01:2025-12-31
```

Replace the cleaning run placeholder with an actual completed run. These are candidate experiment windows, not automatically accepted split boundaries. Each `--window` specifies a unique name and inclusive local dates in `Europe/Oslo`. Windows must not overlap. The expected grid uses elapsed UTC hours, including 23/25-hour DST days. Dates are explicit; there is no default mutable latest snapshot or inferred experiment interval.

`--output-dir` defaults to `data/audits/statnett`. `--top-changes` defaults to 10 and controls the number of largest changes and high/low measurement examples retained per window. Optional `--absolute-change-threshold` must be finite and positive, in unverified provider units. No universal threshold is supplied or learned. If used to influence modeling, choose and justify it independently of held-out outcomes. This audit does not fit a statistical anomaly detector or automatically identify change points.

No API call, database connection, or model dependency is needed. Exit `0` means the descriptive audit completed, including findings; `1` means processing failed, and malformed arguments use argparse's code `2`. Exit status never means research approval.

The audit also accepts `--config experiments/001_hour_ahead_v1/config.json` instead of `--input-dir` and `--window`. This [shared configuration](features.md#shared-configuration-and-commands) pins the cleaned manifest and uses the same windows as feature generation. Do not mix the two input forms. A newly generated audit does not silently replace any pinned audit reference in that file.

## Report Contents

| Area | Evidence |
| --- | --- |
| Input integrity | Completion manifest and all cleaning-output checksums verified before reading and again before publication |
| Research coverage | Expected/present hours, absent intervals, first/last expected-hour presence, and hours not completed at the capture time |
| Consumption completeness | Nulls in existing rows separately from absent rows, availability fraction, contiguous null/absent/unavailable stretches, and longest unavailable stretch |
| Statistical profile | Quantiles, mean, population standard deviation, extrema and example timestamps, zeros and intervals, and local-month profiles |
| Abrupt changes | Descriptive largest absolute hour-to-hour changes and their signed values; optional explicit threshold counts |
| Source context | Known production-disagreement interval and overlap after the documented consumption-divergence onset, plus units/revision/finality/availability limitations |

Changes are computed only between adjacent nonmissing hours within each window. An absent or null hour breaks the comparison; dropping rows never compresses time, and no difference is calculated across split boundaries. A profile is not a feature matrix. Its counts do not determine how many examples will survive future lag requirements.

Production context is labeled separately from consumption. The production API/export scaling interval is March 18, 2025, 00:00 UTC inclusive through April 10, 2025, 00:00 UTC exclusive. The consumption divergence began July 9, 2025, 04:00 UTC; context after that onset is a conservative review interval with no confirmed end, not proof that all subsequent hours are incorrect. These are versioned annotations from the [source investigation](statnett-source-investigation.md), not a new API/export comparison. The audit does not estimate revision frequency or establish which source is authoritative.

Missing research-window hours are absences relative to the selected frozen dataset. They are not automatically provider omissions: the window may extend beyond the capture, or the snapshot may never have included its endpoints. `hours_not_completed_at_capture` helps distinguish one such limitation. Original cleaning `requested_range_coverage` remains unchanged and is carried into the audit for context.

## Outputs And Review

Each execution creates a unique directory containing `audit_report.json` and a completion `manifest.json`, written last. A failed publication removes only the newly created audit directory. Generated audits are ignored by Git; source, normalized, and cleaned artifacts are unchanged.

The manifest links the cleaning manifest hash and output hashes, effective window/settings configuration and deterministic fingerprint, report checksum, audit implementation hashes, Python/pandas versions, execution timestamp, and source-investigation checksum when available. Repeated configurations have matching fingerprints and deterministic profiles but distinct execution IDs. The manifest does not checksum itself. Verify completed output with `validate_research_audit(Path(...))` in [the audit runner](../src/oslo_energy/pipeline/research_audit.py).

`research_eligibility` is always `pending_review`, and `training_ready` remains false. Check statuses distinguish coverage/completeness findings from completed descriptive work and checks that remain `not_run`. Review requires:

1. Confirm the snapshot and proposed split boundaries, including endpoint coverage.
2. Document missing-input and unavailable-label handling. Never fill targets and score them as observed truth; optional production missingness must not exclude consumption-only examples.
3. Investigate material zeros, extremes, abrupt changes, and monthly distribution changes without assuming every spike is erroneous. Descriptive candidates are not automatic exclusions.
4. State source/revision limitations and forecast issue-time availability assumptions. Completed measurements are not necessarily immediately published or final.
5. Explicitly record approval of provisional retrospective API-reference research in the subsequent experiment configuration. Preserve the audit; it is not an automatic eligibility certificate.

Inspecting held-out distributions or labels can influence subsequent choices. This diagnostic audit must not become test-driven tuning of filters, thresholds, features, or models. If held-out outcomes influence substantive experiment choices, select a new untouched final test reference/period and disclose the change. Learned preprocessing remains training-fold scoped.

After review, build versioned lag/calendar feature families, quantify feature-example coverage, then freeze the baseline/linear-regression/MLP comparison as described in the [research plan](research-plan.md).

## Implementation And Tests

- [Window profiling](../src/oslo_energy/transformation/research_audit.py) contains explicit window definitions and pure descriptive computations.
- [Audit runner](../src/oslo_energy/pipeline/research_audit.py) verifies cleaned artifacts, records source context, publishes reports, and verifies completion.
- [CLI](../src/oslo_energy/pipeline/run_research_audit.py) parses explicit windows and reports execution results.

```bash
python -m pytest -q tests/transformation/test_research_profile.py tests/pipeline/test_research_audit.py
```
