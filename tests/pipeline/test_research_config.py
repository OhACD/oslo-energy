"""Verify strict shared configuration and portable chronological split definitions."""

from copy import deepcopy
import pytest

from oslo_energy.ingestion.archive import Archive
from oslo_energy.pipeline.research_config import load_research_config


def configuration():
    return {
        "schema_version": "1", "experiment_id": "first_features_v1", "status": "draft",
        "purpose": "provisional_feature_generation",
        "inputs": {"cleaned": {"path": "../cleaned", "manifest_sha256": "a" * 64}},
        "task": {"target": "consumption", "horizon_hours": 1, "timezone": "Europe/Oslo",
                 "issue_time": "target_start", "publication_delay_hours": 0,
                 "availability_assumption": "completed_hour_plus_configured_delay_retrospective"},
        "windows": [
            {"name": "train", "from_date": "2023-01-01", "to_date": "2023-01-01"},
            {"name": "validation", "from_date": "2024-01-01", "to_date": "2024-01-01"},
            {"name": "test", "from_date": "2025-01-01", "to_date": "2025-01-01"},
        ],
        "features": {"recipe_id": "lag_calendar", "recipe_version": "1", "families": [
            {"id": "consumption_lags", "version": "1", "configuration": {"lags": [1, 24, 168]}},
            {"id": "calendar", "version": "1", "configuration": {}},
        ]},
        "treatment": {"suspicious_values": "retain", "missing_inputs": "preserve", "missing_targets": "preserve", "drop_rows": False},
    }


def test_paths_resolve_relative_to_configuration_and_dates_are_not_hardcoded(tmp_path):
    path = tmp_path / "experiment" / "config.json"
    raw = configuration()
    Archive(path).write(raw)
    original = load_research_config(path)
    assert original.cleaned_dir == tmp_path / "cleaned"
    assert original.windows[0].from_date.year == 2023
    path.unlink()
    raw["windows"][0]["from_date"] = "2022-01-01"
    Archive(path).write(raw)
    assert load_research_config(path).fingerprint != original.fingerprint


@pytest.mark.parametrize("field, value", [("horizon_hours", 2), ("horizon_hours", True),
    ("publication_delay_hours", -1), ("publication_delay_hours", True), ("issue_time", "target_end")])
def test_unsupported_or_leaky_task_configuration_fails(tmp_path, field, value):
    raw = configuration()
    raw["task"][field] = value
    path = tmp_path / "config.json"
    Archive(path).write(raw)
    with pytest.raises(ValueError):
        load_research_config(path)


@pytest.mark.parametrize("broken", ["overlap", "order", "missing_role", "family", "version", "unknown", "fill", "duplicate"])
def test_invalid_shared_contracts_fail(tmp_path, broken):
    raw = configuration()
    if broken == "overlap":
        raw["windows"][0]["to_date"] = "2024-01-01"
    elif broken == "order":
        raw["windows"][0]["name"], raw["windows"][1]["name"] = "validation", "train"
    elif broken == "missing_role":
        raw["windows"].pop()
    elif broken == "family":
        raw["features"]["families"][0]["id"] = "arbitrary.module"
    elif broken == "version":
        raw["features"]["families"][0]["version"] = "2"
    elif broken == "unknown":
        raw["typo"] = True
    elif broken == "fill":
        raw["treatment"]["missing_targets"] = "zero"
    else:
        raw["features"]["families"].append(deepcopy(raw["features"]["families"][0]))
    path = tmp_path / "config.json"
    Archive(path).write(raw)
    with pytest.raises(ValueError):
        load_research_config(path)


def test_feature_generation_requires_explicit_audit_reference(tmp_path):
    path = tmp_path / "config.json"
    Archive(path).write(configuration())
    with pytest.raises(ValueError, match="audit reference"):
        load_research_config(path, require_audit=True)


def test_nonfinite_and_duplicate_json_keys_fail(tmp_path):
    path = tmp_path / "config.json"
    for content in ('{"schema_version":"1","schema_version":"1"}', '{"value":NaN}'):
        path.write_text(content)
        with pytest.raises(ValueError):
            load_research_config(path)