"""Notebook-facing orchestration of immutable audit, feature, and benchmark stages."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pandas as pd

from oslo_energy.benchmarking.config import BenchmarkConfig, load_benchmark_config
from oslo_energy.benchmarking.data import load_benchmark_dataset
from oslo_energy.ingestion.archive import Archive
from oslo_energy.models import load_model
from oslo_energy.pipeline.benchmark import BenchmarkRun, recompute_benchmark_metrics, run_benchmark, validate_benchmark_run
from oslo_energy.pipeline.cleaning import artifact_checksum
from oslo_energy.pipeline.features import FeatureRun, run_features, validate_feature_run
from oslo_energy.pipeline.research_audit import ResearchAuditRun, run_research_audit, validate_research_audit
from oslo_energy.pipeline.research_config import ResearchConfig, load_research_config


def _notebook_content_fingerprint(path: Path) -> str:
    notebook = json.loads(path.read_text(encoding="utf-8"))
    cells = [{"cell_type": cell["cell_type"],
              "source": "".join(cell["source"]) if isinstance(cell["source"], list) else cell["source"]}
             for cell in notebook["cells"]]
    return hashlib.sha256(json.dumps(cells, sort_keys=True).encode()).hexdigest()


@dataclass
class ExperimentExecution:
    """One explicit execution; source settings are snapshots, never rewritten in place."""

    experiment_dir: Path
    run_dir: Path
    feature_config: ResearchConfig
    benchmark_config: BenchmarkConfig
    source_hashes: dict[str, str]
    notebook_fingerprint: str | None = None
    audit_run: ResearchAuditRun | None = None
    feature_run: FeatureRun | None = None
    benchmark_run: BenchmarkRun | None = None
    resolved_configs: dict[str, Path] = field(default_factory=dict)

    @classmethod
    def begin(cls, experiment_dir: Path) -> "ExperimentExecution":
        experiment_dir = experiment_dir.resolve()
        feature = load_research_config(experiment_dir / "feature_config.json", require_audit=True)
        benchmark = load_benchmark_config(experiment_dir / "benchmark.json")
        if benchmark.effective["evaluation_split"] != "validation":
            raise ValueError("The notebook controller supports validation only; final-test scoring stays explicit")
        sources = [feature.path, benchmark.path]
        notebook = experiment_dir / "experiment.ipynb"
        if notebook.is_file():
            sources.append(notebook)
        hashes = {path.name: artifact_checksum(path) for path in sources}
        if hashes[feature.path.name] != feature.sha256 or hashes[benchmark.path.name] != benchmark.sha256:
            raise ValueError("Source configuration changed while preparing the experiment")
        run_dir = experiment_dir / "runs" / f"experiment_{uuid4().hex}"
        run_dir.mkdir(parents=True, exist_ok=False)
        (run_dir / "sources").mkdir()
        for path in sources:
            with (run_dir / "sources" / path.name).open("xb") as destination:
                destination.write(path.read_bytes())
            if artifact_checksum(run_dir / "sources" / path.name) != hashes[path.name]:
                raise ValueError("Source changed while snapshotting experiment inputs")
        notebook_fingerprint = _notebook_content_fingerprint(notebook) if notebook.is_file() else None
        Archive(run_dir / "plan.json").write({
            "schema_version": "1", "experiment_id": feature.effective["experiment_id"],
            "executed_at_utc": datetime.now(timezone.utc).isoformat(), "source_sha256": hashes,
            "scope": "Offline audit -> features -> train-only models -> validation -> plots; no live collection or final-test scoring",
            "feature_binding": "Benchmark settings are retained; a run-local config explicitly pins freshly generated features",
            "notebook_content_fingerprint": notebook_fingerprint,
            "controller_sha256": artifact_checksum(Path(__file__)),
        })
        return cls(experiment_dir, run_dir, feature, benchmark, hashes, notebook_fingerprint)

    def _check_sources(self) -> None:
        for name, checksum in self.source_hashes.items():
            if name == "experiment.ipynb":
                if _notebook_content_fingerprint(self.experiment_dir / name) != self.notebook_fingerprint:
                    raise ValueError("Experiment notebook content changed during execution")
                continue
            if artifact_checksum(self.experiment_dir / name) != checksum:
                raise ValueError("Experiment source changed during execution")

    def audit(self) -> ResearchAuditRun:
        if self.audit_run is not None:
            raise ValueError("Audit already executed; start a new experiment execution to rerun")
        self._check_sources()
        self.audit_run = run_research_audit(
            self.feature_config.cleaned_dir, windows=self.feature_config.windows,
            config_path=self.feature_config.path, output_dir=self.run_dir / "audit",
        )
        return self.audit_run

    def features(self) -> FeatureRun:
        if self.audit_run is None or self.feature_run is not None:
            raise ValueError("Run audit once before generating features")
        self._check_sources()
        validate_research_audit(self.audit_run.output_dir)
        effective = self.feature_config.effective.copy()
        effective["inputs"] = {
            "cleaned": {**effective["inputs"]["cleaned"], "path": str(self.feature_config.cleaned_dir)},
            "audit": {"path": str(self.audit_run.output_dir),
                      "manifest_sha256": artifact_checksum(self.audit_run.output_dir / "manifest.json")},
        }
        path = self.run_dir / "config" / "feature_config.json"
        Archive(path).write(effective)
        self.resolved_configs["features"] = path
        self.feature_run = run_features(path, output_dir=self.run_dir / "features")
        return self.feature_run

    def benchmark(self) -> BenchmarkRun:
        if self.feature_run is None or self.benchmark_run is not None:
            raise ValueError("Generate features once before benchmarking")
        self._check_sources()
        validate_feature_run(self.feature_run.output_dir)
        effective = self.benchmark_config.effective.copy()
        effective["feature_run"] = {
            "path": str(self.feature_run.output_dir),
            "manifest_sha256": artifact_checksum(self.feature_run.output_dir / "manifest.json"),
        }
        path = self.run_dir / "config" / "benchmark.json"
        Archive(path).write(effective)
        self.resolved_configs["benchmark"] = path
        self.benchmark_run = run_benchmark(path, output_dir=self.run_dir / "benchmark")
        if self.benchmark_run.report["status"] == "completed_with_model_failures":
            raise ValueError("A model failed; inspect the benchmark report before declaring experiment completion")
        return self.benchmark_run

    def verify(self) -> dict:
        if self.benchmark_run is None or self.feature_run is None or self.audit_run is None:
            raise ValueError("All stages must complete before experiment verification")
        self._check_sources()
        validate_research_audit(self.audit_run.output_dir)
        validate_feature_run(self.feature_run.output_dir)
        validate_benchmark_run(self.benchmark_run.output_dir)
        metrics = Archive(self.benchmark_run.output_dir / "metrics.json").read()
        if metrics != recompute_benchmark_metrics(self.benchmark_run.output_dir):
            raise ValueError("Saved metrics do not reproduce from predictions")
        config = load_benchmark_config(self.resolved_configs["benchmark"])
        dataset = load_benchmark_dataset(config.feature_dir, expected_manifest_sha256=config.effective["feature_run"]["manifest_sha256"])
        saved = pd.read_csv(self.benchmark_run.output_dir / "predictions.csv", float_precision="round_trip")
        for name in self.benchmark_run.report["models"]:
            model = load_model(self.benchmark_run.output_dir / "models" / name / "state.json")
            predictions = model.predict(dataset.forecast_batch()).values
            original = saved.loc[saved["model_id"] == name].set_index("target_start_utc")["prediction"]
            original.index = pd.to_datetime(original.index, utc=True)
            pd.testing.assert_series_equal(predictions, original, check_names=False, check_exact=False, rtol=1e-12, atol=1e-10)
        return {"artifact_integrity": "passed", "metric_recomputation": "passed", "all_model_reloads": "passed",
                "evaluation_split": "validation", "final_test_scored": False}

    def complete(self) -> dict:
        checks = self.verify()
        stages = {"audit": self.audit_run.output_dir, "features": self.feature_run.output_dir,
                  "benchmark": self.benchmark_run.output_dir}
        report = {
            "schema_version": "1", "run_id": self.run_dir.name, "status": "completed_with_warnings",
            "training_ready": False, "research_eligibility": "pending_review", "checks": checks,
            "stage_directories": {name: str(path.relative_to(self.run_dir)) for name, path in stages.items()},
            "source_sha256": self.source_hashes,
        }
        Archive(self.run_dir / "experiment_report.json").write(report)
        artifacts = [path for path in self.run_dir.rglob("*") if path.is_file()]
        Archive(self.run_dir / "manifest.json").write({
            "schema_version": "1", "run_id": self.run_dir.name,
            "output_sha256": {str(path.relative_to(self.run_dir)): artifact_checksum(path) for path in sorted(artifacts)},
            "stage_manifest_sha256": {name: artifact_checksum(path / "manifest.json") for name, path in stages.items()},
        })
        validate_experiment_run(self.run_dir)
        return report


def validate_experiment_run(run_dir: Path) -> dict:
    manifest = Archive(run_dir / "manifest.json").read()
    if manifest.get("schema_version") != "1" or manifest.get("run_id") != run_dir.name:
        raise ValueError("Incompatible experiment manifest")
    for name, checksum in manifest["output_sha256"].items():
        path = run_dir / name
        if run_dir.resolve() not in path.resolve().parents or artifact_checksum(path) != checksum:
            raise ValueError(f"Experiment artifact integrity failure: {name}")
    report = Archive(run_dir / "experiment_report.json").read()
    if report["training_ready"] is not False or report["checks"]["final_test_scored"] is not False:
        raise ValueError("Experiment restrictions were not retained")
    return manifest