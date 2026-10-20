"""Strict, shared experiment inputs, chronological windows, and feature recipes."""

from copy import deepcopy
from dataclasses import dataclass
from datetime import date
import hashlib
import json
from pathlib import Path
import re
from typing import Sequence

from oslo_energy.transformation.features import get_family
from oslo_energy.transformation.research_audit import ResearchWindow


def validate_windows(windows: Sequence[ResearchWindow], *, split_roles: bool = False) -> tuple[ResearchWindow, ...]:
    if not windows or len({window.name for window in windows}) != len(windows):
        raise ValueError("Provide nonempty, uniquely named research windows")
    ordered = tuple(sorted(windows, key=lambda window: window.start_utc))
    if any(previous.end_utc > following.start_utc for previous, following in zip(ordered, ordered[1:])):
        raise ValueError("Research windows must not overlap")
    if split_roles and tuple(window.name for window in ordered) != ("train", "validation", "test"):
        raise ValueError("Require chronological train, validation, and test windows")
    return ordered


def _keys(value: dict, required: set[str], optional: set[str] | None = None) -> None:
    if not isinstance(value, dict) or not required.issubset(value) or set(value) - required - (optional or set()):
        raise ValueError(f"Expected configuration keys: {sorted(required)}")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON configuration key: {key}")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError(f"Non-finite JSON configuration constant: {value}")


@dataclass(frozen=True)
class ResearchConfig:
    path: Path
    sha256: str
    fingerprint: str
    effective: dict
    windows: tuple[ResearchWindow, ...]
    cleaned_dir: Path
    audit_dir: Path | None


def load_research_config(path: Path, *, require_audit: bool = False) -> ResearchConfig:
    path = path.resolve()
    content = path.read_bytes()
    raw = json.loads(content, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    _keys(raw, {"schema_version", "experiment_id", "status", "purpose", "inputs", "task", "windows", "features", "treatment"})
    if raw["schema_version"] != "1" or raw["status"] != "draft" or raw["purpose"] != "provisional_feature_generation":
        raise ValueError("Unsupported research configuration version/status/purpose")
    if not isinstance(raw["experiment_id"], str) or not re.fullmatch(r"[a-z0-9_]+", raw["experiment_id"]):
        raise ValueError("Invalid experiment identity")
    _keys(raw["inputs"], {"cleaned"}, {"audit"})
    for reference in raw["inputs"].values():
        _keys(reference, {"path", "manifest_sha256"})
        if not isinstance(reference["path"], str) or not reference["path"].strip():
            raise ValueError("Input reference requires a path")
        if not isinstance(reference["manifest_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", reference["manifest_sha256"]):
            raise ValueError("Input reference requires a SHA-256 manifest identity")
    task = raw["task"]
    _keys(task, {"target", "horizon_hours", "timezone", "issue_time", "publication_delay_hours", "availability_assumption"})
    if (
        task["target"] != "consumption" or type(task["horizon_hours"]) is not int or task["horizon_hours"] != 1
        or task["timezone"] != "Europe/Oslo" or task["issue_time"] != "target_start"
        or task["availability_assumption"] != "completed_hour_plus_configured_delay_retrospective"
        or type(task["publication_delay_hours"]) is not int or task["publication_delay_hours"] < 0
    ):
        raise ValueError("Unsupported task or availability assumptions")
    if not isinstance(raw["windows"], list):
        raise ValueError("windows must be an explicit list")
    windows = []
    for item in raw["windows"]:
        _keys(item, {"name", "from_date", "to_date"})
        start, end = date.fromisoformat(item["from_date"]), date.fromisoformat(item["to_date"])
        if start.isoformat() != item["from_date"] or end.isoformat() != item["to_date"]:
            raise ValueError("Window dates must use YYYY-MM-DD")
        windows.append(ResearchWindow(item["name"], start, end))
    ordered = validate_windows(windows, split_roles=True)
    recipe = raw["features"]
    _keys(recipe, {"recipe_id", "recipe_version", "families"})
    if not isinstance(recipe["recipe_id"], str) or not re.fullmatch(r"[a-z0-9_]+", recipe["recipe_id"]) or recipe["recipe_version"] != "1":
        raise ValueError("Unsupported feature recipe identity/version")
    if not isinstance(recipe["families"], list) or not recipe["families"]:
        raise ValueError("Select at least one feature family")
    effective = deepcopy(raw)
    seen, names = set(), set()
    for item, normalized in zip(recipe["families"], effective["features"]["families"]):
        _keys(item, {"id", "version", "configuration"})
        if not isinstance(item["configuration"], dict) or not isinstance(item["id"], str):
            raise ValueError("Feature family configuration must be an object with a known ID")
        family = get_family(item["id"], item["version"], item["configuration"])
        if family.family_id in seen or names.intersection(family.columns):
            raise ValueError("Duplicate feature families or output names")
        seen.add(family.family_id)
        names.update(family.columns)
        normalized["configuration"] = family.configuration
    expected_treatment = {"suspicious_values": "retain", "missing_inputs": "preserve", "missing_targets": "preserve", "drop_rows": False}
    if raw["treatment"] != expected_treatment or raw["treatment"].get("drop_rows") is not False:
        raise ValueError("Initial features require preservation without filling or dropping rows")
    audit_reference = raw["inputs"].get("audit")
    if require_audit and audit_reference is None:
        raise ValueError("A matching audit reference is required for real feature generation")
    effective["windows"] = [{"name": window.name, "from_date": window.from_date.isoformat(),
                             "to_date": window.to_date.isoformat()} for window in ordered]
    fingerprint = hashlib.sha256(json.dumps(effective, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return ResearchConfig(
        path, hashlib.sha256(content).hexdigest(), fingerprint, effective, ordered,
        (path.parent / raw["inputs"]["cleaned"]["path"]).resolve(),
        (path.parent / audit_reference["path"]).resolve() if audit_reference else None,
    )