# Statnett Source Investigation

API/export evidence collected on **October 6, 2026**. Revision evidence and project status updated on **October 13, 2026**.

## Summary

Statnett's API and annual exports do not always report the same production and consumption values for matched periods. The differences occur at hourly timestamps, before our daily aggregation. We have not established independent ground truth or received a provider explanation.

The project uses the **hourly API as its ingestion source** because it makes missing measurements and daily aggregation easier to inspect. This is a source-selection decision, not proof that API values are correct. Normalized observations remain marked `training_ready: false`.

The main findings are:

- Daily API values and summed hourly API values agree on the audited dates, including one partially missing day.
- API and export values disagree, with a temporary roughly fourfold production difference and changing consumption differences.
- CSV and Excel contain the same 2025 export values, so CSV parsing alone does not explain the mismatch.
- Repeated API captures between October 6 and October 13 revealed changes to 22 completed hourly observations; see [observed API revisions](#observed-api-revisions).

We are awaiting Statnett's answers about the differences, measurement meaning, and revisions. See [open questions](#open-questions) for what remains unresolved and [reproduction](#reproduction) for the saved evidence and research command.

## Why We Use the Hourly API

The investigation began with daily API values compared against hourly CSV values summed over Norwegian local days. Three comparisons must be distinguished:

| Comparison | Observation |
| --- | --- |
| Daily API vs summed hourly API | Exact agreement on audited dates |
| Daily API vs summed hourly CSV export | Production and consumption differences |
| Hourly API vs hourly CSV at matched UTC times | Differences before daily aggregation |

Ingestion already used the API. The change was **daily API to hourly API**, not CSV to API. Existing daily archives remain unchanged and replayable.

Hourly observations let us inspect every contribution to a daily total, identify missing measurements, and require all expected hours. UTC timestamps keep repeated autumn local hours distinct; Norwegian local dates have 23, 24, or 25 hours. New hourly responses also avoid the legacy daily response's padding convention.

The [pipeline guide](pipeline.md#technical-reference) documents requests and normalization. Future analytical datasets should derive from archived hourly API observations through documented transformations. Exports remain comparison evidence rather than interchangeable training inputs. No blanket scaling or imputation has been applied.

## Findings

### Internal API Agreement

An earlier audit of 1,001 dates in 2024-2026 found exact agreement between daily API values and summed hourly API values for both metrics. This is internal consistency, not independent accuracy validation: the products may share a source or calculation.

November 17, 2025 illustrates the limitation. Only 22 consumption hours have values, but the daily API total still matches their sum. Agreement does not make that a complete day.

### API and Export Disagreement

Differences remain when API and export observations are matched by UTC timestamp. Simple hour shifts and rounding do not reconcile them, and differences outside daylight-saving transitions rule out DST as the sole cause.

CSV and Excel have identical timestamps and values across all 8,761 rows of the 2025 export. That rules out a CSV-only parsing explanation, but does not identify the underlying source or backend processing responsible for the difference.

### Production Scaling Interval

For **552 consecutive hours**, exported production is approximately one quarter of API production. The interval is **March 18, 2025, 00:00 UTC inclusive to April 10, 2025, 00:00 UTC exclusive**. The local boundaries are 01:00 CET and 02:00 CEST respectively.

The median API/export ratio is **4.02675**. Multiplying exported production by four within the interval reduces the median absolute difference to **0.9812%**, compared with **0.9843%** outside it. At the first affected hour, production is 20,883 in the API and 5,175.69 in the export, while consumption agrees within rounding: 16,867 and 16,867.48.

This is evidence of a temporary scaling, unit, or resolution inconsistency. A quarter-hour conversion issue is a plausible explanation, **not a confirmed cause**. We have not corrected the measurements or assumed either product is authoritative.

### Consumption Changes During 2025

- January-June: 4,260 of 4,343 paired hours agree within 0.5 provider units; the median absolute hourly difference is 0.00158%.
- July 9: differences exceed rounding from **06:00 CEST (04:00 UTC)** and become substantially larger from 08:00 CEST. The change did not begin on July 1.
- July-December: the median absolute hourly difference is **1.32232%**.

Production and consumption differences partly track each other afterward, but their hourly difference correlation is only about 0.590 after July 9 and 0.638 in 2026. A shared production or net-exchange change does not explain every residual. The near-zero 2025 daily median below hides the second-half disagreement.

## Observed API Revisions

Repeated captures between **October 6 and October 13, 2026** returned different measurements for the same completed UTC hours. The database revision ledger contained **22 change records affecting 22 distinct hours** after the October 13 audit: 17 detected on October 8 and five additional changes detected on October 13. These are changes within the API product, separate from the API/export disagreement above.

At that checkpoint, the database contained **38 ingestion runs**, with a highest run ID of 52. Run IDs are identifiers, not counts. Requests covered different ranges and overlaps; this was a selective audit, not an exhaustive re-fetch of all historical data. Neither the run count nor the current dataset size is an appropriate denominator for a revision rate.

### October 13 Capture

Daily catch-up stored 120 completed hours for October 8-12 in run 51, with no absent hours or null measurements. A separate request for October 1-12 stored 288 completed hours in run 52 and detected the following changes relative to run 50. All measurements and differences below are in **unverified provider units**, not confirmed MW or MWh.

| Observation start (UTC) | Old production | New production | Old consumption | New consumption |
| --- | ---: | ---: | ---: | ---: |
| 2026-10-07 07:00 | 20,993 | 21,020 | 16,079 | 16,106 |
| 2026-10-07 08:00 | 19,714 | 19,715 | 15,888 | 15,888 |
| 2026-10-07 09:00 | 18,304 | 18,304 | 15,659 | 15,660 |
| 2026-10-07 15:00 | 21,389 | 21,389 | 16,093 | 16,092 |
| 2026-10-07 21:00 | 17,892 | 17,892 | 14,835 | 14,834 |

The previous capture was fetched at `2026-10-08T03:46:11.100769Z`; the replacement capture was fetched at `2026-10-13T03:30:33.249630Z`. These timestamps bound our observations of the two versions, not the exact provider revision time. The largest change in this five-hour sample was +27 in both metrics at 07:00 UTC.

Here, "silent revision" means a changed value detected by comparing captures, without a provider revision timestamp in the captured response. It does not establish that Statnett made no announcement elsewhere, which version is more accurate, or why the values changed. The findings demonstrate that completed observations are not necessarily immutable; they do not establish revision frequency, finality, or measurable forecasting impact.

### Evidence and Point-in-Time Implications

The [storage implementation](persistence.md#corrections-and-retries) records old/new measurements, previous/new run IDs, the new capture's fetch time, and database recording time. Run records identify raw archive paths and SHA-256 checksums. The raw snapshots themselves remain on disk, not in the database; losing those files would weaken reproduction. Neither fetch time nor database recording time is a provider publication timestamp.

For publication, retain a dated export of the revision ledger and linked run metadata, both raw archives for each comparison, and checksum manifests. The following read-only query retrieves the October 13 examples and their provenance; local run IDs identify this collection and are not portable dataset identifiers:

```sql
SELECT revision.period_start_utc,
       revision.old_production, revision.new_production,
       revision.old_consumption, revision.new_consumption,
       previous.id AS previous_run_id, previous.fetched_at AS previous_fetched_at,
       previous.archive_path AS previous_archive, previous.archive_sha256 AS previous_sha256,
       replacement.id AS new_run_id, replacement.fetched_at AS new_fetched_at,
       replacement.archive_path AS new_archive, replacement.archive_sha256 AS new_sha256,
       revision.recorded_at
FROM statnett_observation_revisions AS revision
JOIN statnett_ingestion_runs AS previous ON previous.id = revision.previous_run_id
JOIN statnett_ingestion_runs AS replacement ON replacement.id = revision.new_run_id
WHERE revision.new_run_id = 52
ORDER BY revision.period_start_utc;
```

Rebuilding lag features from current database values can change historical inputs; revised targets can also change scores for fixed predictions. Using retrospectively revised inputs can distort an operational backtest, but the direction and magnitude are unmeasured here. A tested as-of selection mechanism must select only versions captured by each forecast issue time and leave unavailable inputs unavailable. Collection cannot reconstruct versions published before our first capture, and selective refreshes can miss intermediate or older changes.

Future audits should report the overlapping hours actually compared, per-metric revision counts and magnitudes, and observation age at detection. Keep evaluation-reference snapshots fixed and compare capture-available versus retrospectively revised inputs under otherwise identical experiments. See the [research plan](research-plan.md#revision-aware-evaluation) for the proposed evaluation, not implemented results.

## Daily Consumption Comparison

![Percentage difference between daily API consumption and hourly CSV consumption summed over complete Norwegian local days in 2024, 2025, and 2026](research/statnett/consumption_discrepancy.png)

This chart shows **disagreement between products**, not forecast error or error against known ground truth. Each point compares the same Norwegian local day:

$$
\text{difference (\%)} = 100 \times
\frac{\text{daily API consumption} - \sum \text{hourly CSV consumption}}
     {\sum \text{hourly CSV consumption}}
$$

Positive values mean the API total is larger; negative values mean it is smaller. The CSV denominator is a comparison convention, not an endorsement of accuracy. Zero denominators have no percentage result.

The chart generator reconstructs the daily API side by summing hourly API observations. All **999 plotted consumption totals** were verified to equal the original daily-ingestion archive exactly. The [saved table](research/statnett/consumption_discrepancy.csv) calls this hourly-derived total `api_consumption`. That equality applies to the documented snapshots, not necessarily later re-fetches.

### Completeness Rules

Include a local date only when both products contain every expected 23, 24, or 25 unique UTC hours, all paired consumption measurements are finite and nonmissing, and every API hour has ended by the original fetch time.

Incomplete days have no totals or percentages in the saved table; gaps break the chart line. Missing measurements are not replaced with zero or silently included in partial totals.

### Results From the October 6 Snapshots

The 2026 comparable range ends on October 5. P90 is the 90th percentile of absolute daily differences.

| Year | Complete dates | Mean signed difference | Median absolute difference | P90 absolute difference | Maximum absolute difference |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2024 | 365 | +2.35718% | 2.43061% | 3.73079% | 5.13771% |
| 2025 | 356 | +0.21722% | 0.00096% | 1.88790% | 4.72203% |
| 2026 | 278 | +1.11989% | 1.21000% | 2.01543% | 3.23205% |

There are **999 complete comparable dates**. Excluded dates are November 21, 2024, which lacks one CSV hour; November 17-25, 2025, which have missing API measurements; and the partial October 6, 2026. November 17 has two missing API hours; November 18-25 have all hours missing.

**Correction to preliminary results:** an earlier calculation included November 17's 22-hour API sum against a complete CSV day. It reported 357 comparable 2025 dates and a maximum difference of 6.97125%. Excluding that invalid comparison gives 356 dates and a maximum of 4.72203%.

## Structural Checks

These checks establish that the observations are mapped consistently. They do not establish measurement accuracy or finality.

### Hourly Snapshots

The October 6 full-range snapshot normalized to **190,753 hourly slots across 7,949 local dates**, with no padding and 293 missing measurements per series. The completed hourly periods had unique, contiguous UTC identities.

Bounded annual requests overlapped the full snapshot on 8,784 hours in 2024, 8,760 in 2025, and 6,673 in 2026. Production, consumption, and nulls matched exactly in each overlap. Spring and autumn transition checks produced 23-hour and 25-hour local days.

The March 18/April 10 production boundaries and July 9 consumption transition retained complete 24-hour local dates. November 17 had two nulls per series and November 18 had 24; normalization preserved them. These are snapshot-specific counts, not promises about future responses.

### Legacy Daily Snapshots

The original daily response fetched on October 6 contained 7,970 slots per array over 7,949 Norwegian dates. Of 31 nulls per metric, 21 were padding after autumn DST days from 2005 through 2025; the remaining 10 were genuine missing measurements. The extra slots were not a forecast tail.

Replay produced 7,948 earlier-date candidates and one incomplete fetch date, without inventing future dates. The three retained daily examples satisfy the same rule, and replay left raw checksums unchanged. See [legacy daily compatibility](pipeline.md#legacy-daily-compatibility) for the implemented contract. These daily counts must not be confused with current hourly observations.

## Open Questions

We are awaiting answers from Statnett. The provider contact is `firmapost@statnett.no`; no provider-confirmed explanation is recorded here.

| Question | Why it matters |
| --- | --- |
| Why does production differ by roughly fourfold during March 18-April 10, 2025? Is quarter-hour conversion involved? | Determines whether a correction is justified; we cannot infer one from the ratio alone |
| What changed around July 9, 2025, 04:00-06:00 UTC? | Helps explain the consumption divergence and whether it indicates a processing or source change |
| Are values hourly mean power or integrated energy, and what are the units and aggregation windows? | Determines how targets, aggregates, and forecast errors should be interpreted |
| Which underlying sources feed hourly `GetData` and the annual exports? | Establishes whether comparisons provide independent evidence |
| When are estimates final, how are revisions published, and are publication timestamps available? | Determines what information could have been available at a historical forecast date |

Published descriptions use "MW per hour" for exports and describe Norway overview values as ENTSO-E sourced. Those descriptions do not resolve the precise `GetData` semantics or the discrepancy. ENTSO-E cannot be assumed to be an independent benchmark without tracing the data lineage.

The evidence points to inconsistent source values or processing in the export data path, especially the production interval, but does not identify a server bug. Possible causes include stored source data, conversion, aggregation, or revisions. Neither product has been independently validated.

Structurally valid candidates can be [stored with provenance](persistence.md) while questions remain open. Storage is not training approval. Repeated collection can capture later observed corrections, but neither database time nor fetch time proves when the provider originally published a value. The [research plan](research-plan.md) explains the implications for evaluation.

## Reproduction

From the repository root, with the project environment active:

```bash
python -m pip install -e '.[dev,research]'
python -m oslo_energy.pipeline.compare_consumption
```

This is a **live research command**, not an offline replay of the published chart. It contacts Statnett, archives decoded API JSON and original CSV bytes under `data/raw/statnett/comparison/`, and writes the chart, comparison table, and timestamped provenance under `docs/research/statnett/`. It can replace the existing chart and table; preserve them before running if you need to retain the published snapshot. Matplotlib is required only for plotting, not ordinary ingestion.

Provenance records URLs, requested dates, separate fetch times, archive paths, SHA-256 checksums, completeness rules, and statistics. The [October 6 provenance record](research/statnett/provenance_20261006T020353656523Z.json) accompanies the saved chart and table. The table records exclusions as well as plotted dates.

Later requests may return revised data or different coverage. Exact reconstruction requires the original raw inputs, which are not tracked in Git; the committed comparison artifacts alone are not a complete raw dataset. Keep those inputs and their checksums when publishing a new experiment.

## Sources and Data Rights

- [Official chart request implementation](https://driftsdata.statnett.no/web/Scripts/ProductionConsumption/ProductionConsumptionDataService.js)
- [Statnett download definitions](https://www.statnett.no/for-aktorer-i-kraftbransjen/tall-og-data-fra-kraftsystemet/last-ned-grunndata/)
- [2025 CSV endpoint](https://driftsdata.statnett.no/restapi/Download/productionconsumption/2025?fileFormat=csv)
- [Published source descriptions](https://driftsdata.statnett.no/restapi/Translator/Translations?language=en)

The project's MIT license does not cover Statnett's underlying data. Confirm provider terms before redistribution or production use.
