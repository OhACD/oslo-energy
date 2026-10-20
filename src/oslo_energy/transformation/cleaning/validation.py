"""Shared policy-output invariants, independent of individual policy decisions."""

import pandas as pd

from oslo_energy.transformation.cleaning.contracts import (
    IDENTITY_COLUMNS, MEASUREMENTS, OBSERVATION_COLUMNS, CleaningContext, CleaningResult, PolicyCapabilities,
)
from oslo_energy.transformation.statnett_normalizer import StatnettNormalizer


def validate_policy_result(
    context: CleaningContext, result: CleaningResult, capabilities: PolicyCapabilities,
) -> None:
    """Verify identity, originals, capabilities, and explicit change declarations."""
    source = context.observations.set_index("period_start_utc", drop=False)
    output = result.observations
    required = {*OBSERVATION_COLUMNS, *(f"{metric}_original" for metric in MEASUREMENTS)}
    if not required.issubset(output.columns):
        raise ValueError("Policy output is missing required observation/original columns")
    if output["period_start_utc"].duplicated().any():
        raise ValueError("Policy output has duplicate UTC identities")
    if not output["period_start_utc"].is_monotonic_increasing:
        raise ValueError("Policy output must preserve UTC ordering")
    for metric in MEASUREMENTS:
        StatnettNormalizer._measurements(
            [None if pd.isna(value) else value for value in output[metric]], metric,
        )
    target = output.set_index("period_start_utc", drop=False)
    excluded = source.index.difference(target.index)
    synthetic = target.index.difference(source.index)
    if len(excluded) and not capabilities.can_exclude:
        raise ValueError("Policy may not exclude observations")
    if len(synthetic) and not capabilities.can_synthesize:
        raise ValueError("Policy may not synthesize observations")
    common = source.index.intersection(target.index)
    try:
        pd.testing.assert_frame_equal(
            source.loc[common, list(IDENTITY_COLUMNS)],
            target.loc[common, list(IDENTITY_COLUMNS)], check_dtype=False,
        )
        for metric in MEASUREMENTS:
            pd.testing.assert_series_equal(
                source.loc[common, metric], target.loc[common, f"{metric}_original"],
                check_names=False, check_dtype=False, check_exact=True,
            )
    except AssertionError as exc:
        raise ValueError("Policy changed source identity, provenance, or original values") from exc
    for metric in MEASUREMENTS:
        before, after = source.loc[common, metric], target.loc[common, metric]
        changed = ~(before.eq(after) | (before.isna() & after.isna()))
        if changed.any() and not capabilities.can_modify:
            raise ValueError("Policy may not modify measurements")
        for timestamp in common[changed]:
            if not any(
                record.action == "modify" and record.metric == metric
                and record.period_start_utc == timestamp.isoformat()
                for record in result.actions
            ):
                raise ValueError("Policy changed a value without a modify action")
    for timestamps, action in ((excluded, "exclude"), (synthetic, "synthesize")):
        for timestamp in timestamps:
            if not any(
                record.action == action and record.period_start_utc == timestamp.isoformat()
                for record in result.actions
            ):
                raise ValueError(f"Policy requires an explicit {action} action")
    if any(record not in result.findings for record in context.findings):
        raise ValueError("Policy may not suppress diagnostic findings")
    if any(record.kind != "diagnostic" for record in result.findings):
        raise ValueError("Findings must be diagnostic records")
    if any(record.kind != "policy" for record in result.actions):
        raise ValueError("Actions must be policy records")
    source_ids = {timestamp.isoformat() for timestamp in source.index}
    target_ids = {timestamp.isoformat() for timestamp in target.index}
    known_ids = source_ids | target_ids
    retained_ids = source_ids & target_ids
    permissions = {"retain": True, "exclude": capabilities.can_exclude,
                   "modify": capabilities.can_modify, "synthesize": capabilities.can_synthesize}
    covered = set()
    action_keys = set()
    for record in result.actions:
        if record.action not in permissions or not permissions[record.action]:
            raise ValueError("Policy action contradicts declared capabilities")
        if record.period_start_utc not in known_ids:
            raise ValueError("Policy action has no observation linkage")
        if record.metric is not None and record.metric not in MEASUREMENTS:
            raise ValueError("Policy action refers to an unknown metric")
        if record.action == "retain" and record.period_start_utc not in retained_ids:
            raise ValueError("Retain action must link an unchanged source identity")
        key = (record.action, record.period_start_utc, record.metric)
        if key in action_keys:
            raise ValueError("Duplicate policy action")
        action_keys.add(key)
        covered.add(record.period_start_utc)
    if not source_ids.issubset(covered):
        raise ValueError("Every input observation requires an explicit policy outcome")