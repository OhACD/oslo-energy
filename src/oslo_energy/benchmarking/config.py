"""Benchmark settings are separate from immutable feature-generation settings."""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

from oslo_energy.models.registry import get_model
from oslo_energy.benchmarking.plotting import get_plot
from oslo_energy.pipeline.research_config import _invalid_constant, _keys, _unique_object


@dataclass(frozen=True)
class BenchmarkConfig:
    path: Path
    sha256: str
    fingerprint: str
    effective: dict
    feature_dir: Path | None


def load_benchmark_config(path: Path, *, allow_generated_features: bool = False) -> BenchmarkConfig:
    path = path.resolve()
    content = path.read_bytes()
    raw = json.loads(content, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    _keys(raw, {"schema_version", "benchmark_id", "feature_run", "models", "baselines", "provisional_research"}, {"evaluation_split", "plots"})
    if raw["schema_version"] != "1" or raw["provisional_research"] is not True:
        raise ValueError("Require version 1 and explicit provisional retrospective research permission")
    if not isinstance(raw["benchmark_id"], str) or not re.fullmatch(r"[a-z0-9_]+", raw["benchmark_id"]):
        raise ValueError("Invalid benchmark identity")
    reference = raw["feature_run"]
    generated = reference == {"source": "experiment_features"}
    if generated:
        if not allow_generated_features:
            raise ValueError("Generated features require the notebook experiment controller; standalone benchmarks need a pinned feature run")
    else:
        _keys(reference, {"path", "manifest_sha256"})
        if not isinstance(reference["path"], str) or not reference["path"].strip():
            raise ValueError("Require an explicit feature-run path")
        if not isinstance(reference["manifest_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", reference["manifest_sha256"]):
            raise ValueError("Require a pinned feature manifest SHA-256")
    if raw.get("evaluation_split", "validation") not in ("validation", "test"):
        raise ValueError("Benchmark split must be validation or test")
    if not isinstance(raw["models"], list) or not raw["models"]:
        raise ValueError("Select at least one registered model")
    specs, seen = [], set()
    for item in raw["models"]:
        _keys(item, {"id", "version", "configuration"})
        if not isinstance(item["id"], str) or not isinstance(item["configuration"], dict):
            raise ValueError("Invalid model ID or configuration")
        model = get_model(item["id"], item["version"], item["configuration"])
        if model.model_id in seen:
            raise ValueError("Model IDs must be unique within a benchmark")
        seen.add(model.model_id)
        specs.append({"id": model.model_id, "version": model.version, "configuration": model.configuration})
    baselines = raw["baselines"]
    if not isinstance(baselines, list) or not baselines or any(not isinstance(name, str) for name in baselines):
        raise ValueError("Require explicit baseline identities")
    if len(set(baselines)) != len(baselines) or set(baselines) - seen or any(get_model(name).requires_fit for name in baselines):
        raise ValueError("Baselines must name distinct selected unfitted reference methods")
    plots = raw.get("plots", ["comparison"])
    if not isinstance(plots, list) or any(not isinstance(name, str) for name in plots) or len(set(plots)) != len(plots):
        raise ValueError("plots must select distinct registered plot IDs")
    for plot_id in plots:
        get_plot(plot_id)
    effective = {**raw, "models": specs, "evaluation_split": raw.get("evaluation_split", "validation"), "plots": plots}
    fingerprint = hashlib.sha256(json.dumps(effective, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return BenchmarkConfig(path, hashlib.sha256(content).hexdigest(), fingerprint, effective,
                           None if generated else (path.parent / reference["path"]).resolve())