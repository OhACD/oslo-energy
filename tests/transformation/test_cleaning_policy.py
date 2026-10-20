"""Verify policy preservation and the reusable capability contract offline."""

from dataclasses import replace

import pandas as pd
import pytest

from oslo_energy.transformation.cleaning import (
    OBSERVATION_COLUMNS,
    CleaningContext,
    PreserveAndFlagPolicy,
    PolicyCapabilities,
    QualityRecord,
    get_policy,
)


@pytest.fixture
def context():
    starts = pd.date_range("2025-01-01", periods=3, freq="h", tz="UTC")
    observations = pd.DataFrame({
        "period_start_utc": starts, "period_end_utc": starts + pd.Timedelta(hours=1),
        "observation_date": starts.tz_convert("Europe/Oslo").date,
        "period_hours": 1, "source_index": range(3),
        "production": [1.0, float("nan"), 3.0], "consumption": [4.0, 5.0, float("nan")],
    })
    finding = QualityRecord("diagnostic", "missing_measurement", metric="production")
    return CleaningContext(observations, {"training_ready": False}, (finding,))


def test_preserve_and_flag_keeps_identity_values_nulls_and_findings(context):
    original = context.observations.copy(deep=True)
    result = get_policy("preserve_and_flag").apply(context)

    pd.testing.assert_frame_equal(result.observations[list(OBSERVATION_COLUMNS)], original)
    pd.testing.assert_frame_equal(context.observations, original)
    for metric in ("production", "consumption"):
        pd.testing.assert_series_equal(
            result.observations[metric], result.observations[f"{metric}_original"],
            check_names=False,
        )
    assert result.findings == context.findings
    assert len(result.actions) == 3
    assert {record.action for record in result.actions} == {"retain"}


def test_policy_registry_and_configuration_are_explicit():
    with pytest.raises(ValueError, match="Unknown cleaning policy"):
        get_policy("some.module.Policy")
    with pytest.raises(ValueError, match="no configuration"):
        get_policy("preserve_and_flag", {"fill": 0})


def test_public_exports_resolve_to_package_implementations():
    from oslo_energy.transformation import cleaning
    from oslo_energy.transformation.cleaning import base, contracts, registry, validation
    from oslo_energy.transformation.cleaning.policies.preserve_and_flag import PreserveAndFlagPolicy as Policy

    assert cleaning.CleaningPolicy is base.CleaningPolicy
    assert cleaning.PreserveAndFlagPolicy is Policy
    assert cleaning.validate_policy_result is validation.validate_policy_result
    assert cleaning.get_policy is registry.get_policy
    assert cleaning.POLICY_REGISTRY is registry.POLICY_REGISTRY
    for name in (
        "IDENTITY_COLUMNS", "MEASUREMENTS", "OBSERVATION_COLUMNS", "CleaningContext",
        "CleaningResult", "PolicyCapabilities", "QualityRecord",
    ):
        assert getattr(cleaning, name) is getattr(contracts, name)
    assert isinstance(cleaning.get_policy("preserve_and_flag"), Policy)


@pytest.mark.parametrize("change", ["exclude", "modify", "original", "identity", "findings"])
def test_base_contract_rejects_undeclared_changes(context, change):
    class BrokenPolicy(PreserveAndFlagPolicy):
        def _apply(self, isolated):
            result = super()._apply(isolated)
            if change == "exclude":
                return replace(result, observations=result.observations.iloc[1:].copy())
            if change == "findings":
                return replace(result, findings=())
            column = {"modify": "consumption", "original": "consumption_original",
                      "identity": "source_index"}[change]
            result.observations.loc[0, column] = 999
            return result

    with pytest.raises(ValueError):
        BrokenPolicy().apply(context)


def test_policy_receives_isolated_inputs(context):
    class MutatingPolicy(PreserveAndFlagPolicy):
        def _apply(self, isolated):
            isolated.observations.loc[0, "consumption"] = 999
            isolated.normalization_report["training_ready"] = True
            return super()._apply(isolated)

    with pytest.raises(ValueError):
        MutatingPolicy().apply(context)
    assert context.observations.loc[0, "consumption"] == 4
    assert context.normalization_report["training_ready"] is False


def test_empty_input_is_preserved(context):
    empty = replace(context, observations=context.observations.iloc[:0], findings=())
    result = get_policy("preserve_and_flag").apply(empty)
    assert result.observations.empty
    assert result.actions == ()


@pytest.mark.parametrize("broken", ["missing", "unlinked", "duplicate", "undeclared"])
def test_policy_action_contract_is_enforced(context, broken):
    class BrokenActionsPolicy(PreserveAndFlagPolicy):
        def _apply(self, isolated):
            result = super()._apply(isolated)
            if broken == "missing":
                return replace(result, actions=())
            record = result.actions[0]
            if broken == "unlinked":
                record = replace(record, period_start_utc="2000-01-01T00:00:00+00:00")
            elif broken == "undeclared":
                record = replace(record, action="modify", metric="consumption")
            return replace(result, actions=(*result.actions, record))

    with pytest.raises(ValueError):
        BrokenActionsPolicy().apply(context)


def test_future_policy_can_modify_through_the_shared_contract(context):
    class ExplicitChangePolicy(PreserveAndFlagPolicy):
        policy_id = "test_explicit_change"
        capabilities = PolicyCapabilities(can_modify=True)

        def _apply(self, isolated):
            result = super()._apply(isolated)
            result.observations.loc[0, "consumption"] = 7.0
            action = QualityRecord(
                "policy", "test_change", period_start_utc=isolated.observations.loc[0, "period_start_utc"].isoformat(),
                metric="consumption", action="modify",
            )
            return replace(result, actions=(*result.actions, action))

    result = ExplicitChangePolicy().apply(context)
    assert result.observations.loc[0, "consumption"] == 7
    assert result.observations.loc[0, "consumption_original"] == 4
    assert result.counts["policy_actions"] == 4