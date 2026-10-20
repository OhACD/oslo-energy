"""Verified feature-run handoff and train-only complete-case selection."""

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from oslo_energy.ingestion.archive import Archive
from oslo_energy.models.contracts import ForecastBatch, TrainingBatch, validate_features, validate_numeric
from oslo_energy.pipeline.cleaning import artifact_checksum
from oslo_energy.pipeline.features import load_feature_matrix, validate_feature_run
from oslo_energy.pipeline.research_config import validate_windows
from oslo_energy.transformation.features import get_family
from oslo_energy.transformation.research_audit import ResearchWindow


def _time_column(values: pd.Series, *, missing: bool = False) -> pd.Series:
    def parse(value):
        if missing and value == "":
            return pd.NaT
        timestamp = pd.Timestamp(value)
        if pd.isna(timestamp) or timestamp.tzinfo is None:
            raise ValueError("Benchmark timestamps must be timezone aware")
        return timestamp.tz_convert("UTC")

    return pd.to_datetime(values.map(parse), utc=True)


@dataclass(frozen=True)
class BenchmarkDataset:
    """Fixed features and a separate train/evaluation reference; never random splits."""

    features: pd.DataFrame
    labels: pd.Series
    metadata: pd.DataFrame
    windows: tuple[ResearchWindow, ...]
    evaluation_split: str
    publication_delay_hours: int
    feature_manifest_sha256: str
    feature_report: dict

    @property
    def fit_cutoff(self) -> pd.Timestamp:
        return self.windows[1].start_utc

    def training_batch(self, columns: tuple[str, ...]) -> TrainingBatch:
        if set(columns) - set(self.features.columns):
            raise ValueError("Required model feature columns are missing")
        train = self.metadata["split"].eq("train")
        features = self.features.loc[train, list(columns)].copy()
        labels = self.labels.reindex(features.index)
        available_at = self.metadata.loc[train, "target_end_utc"] + pd.Timedelta(hours=self.publication_delay_hours)
        eligible = features.notna().all(axis=1) & labels.notna() & available_at.le(self.fit_cutoff)
        return TrainingBatch(
            features.loc[eligible], labels.loc[eligible], self.metadata.loc[features.index[eligible], "split"].copy(),
            available_at.loc[eligible], self.windows[0].start_utc, self.windows[0].end_utc, self.fit_cutoff,
            self.feature_manifest_sha256, int(train.sum()),
        )

    def forecast_batch(self) -> ForecastBatch:
        selected = self.metadata["split"].eq(self.evaluation_split)
        return ForecastBatch(self.features.loc[selected].copy(), self.metadata.loc[selected, "forecast_issue_time_utc"].copy())

    def reference(self) -> pd.Series:
        return self.labels.reindex(self.forecast_batch().features.index).copy()


def load_benchmark_dataset(
    feature_dir: Path, *, expected_manifest_sha256: str, evaluation_split: str = "validation",
    allow_test_evaluation: bool = False,
) -> BenchmarkDataset:
    if evaluation_split not in ("validation", "test"):
        raise ValueError("Evaluate only validation or explicitly authorized test windows")
    if evaluation_split == "test" and not allow_test_evaluation:
        raise ValueError("Final-test evaluation requires explicit opt-in")
    if artifact_checksum(feature_dir / "manifest.json") != expected_manifest_sha256:
        raise ValueError("Feature manifest does not match the pinned reference")
    validate_feature_run(feature_dir)
    report = Archive(feature_dir / "feature_report.json").read()
    config = report["effective_configuration"]
    task = config["task"]
    if (
        task["target"] != "consumption" or type(task["horizon_hours"]) is not int or task["horizon_hours"] != 1
        or task["issue_time"] != "target_start" or task["timezone"] != "Europe/Oslo"
        or task["availability_assumption"] != "completed_hour_plus_configured_delay_retrospective"
    ):
        raise ValueError("Unsupported benchmark forecasting task")
    delay = task["publication_delay_hours"]
    if type(delay) is not int or delay < 0:
        raise ValueError("Invalid benchmark publication delay")
    windows = validate_windows([
        ResearchWindow(item["name"], date.fromisoformat(item["from_date"]), date.fromisoformat(item["to_date"]))
        for item in config["windows"]
    ], split_roles=True)
    grids = [pd.date_range(window.start_utc, window.end_utc, freq="h", inclusive="left") for window in windows]
    expected = grids[0].append(grids[1:]).rename("target_start_utc")
    features = load_feature_matrix(feature_dir)
    validate_features(features)
    raw = pd.read_csv(feature_dir / "examples.csv", dtype=str, keep_default_na=False)
    raw.index = _time_column(raw.pop("target_start_utc"))
    if not raw.index.equals(expected) or not features.index.equals(expected):
        raise ValueError("Benchmark examples must exactly match configured target grids")
    splits = pd.Series([window.name for window, grid in zip(windows, grids) for _ in grid], index=expected)
    if not raw["split"].eq(splits).all():
        raise ValueError("Example split labels contradict configured temporal windows")
    for column in ("target_end_utc", "forecast_issue_time_utc"):
        raw[column] = _time_column(raw[column])
    if not raw["target_end_utc"].eq(expected + pd.Timedelta(hours=1)).all() or not raw["forecast_issue_time_utc"].eq(expected).all():
        raise ValueError("Target and issue boundaries contradict the one-hour task")
    quality = pd.read_csv(feature_dir / "feature_quality.csv", dtype=str, keep_default_na=False)
    quality.index = _time_column(quality.pop("target_start_utc"))
    if not quality.index.equals(expected):
        raise ValueError("Feature quality does not match the intended target grid")
    for specification in config["features"]["families"]:
        family = get_family(specification["id"], specification["version"], specification["configuration"])
        for column in family.columns:
            flag = quality[f"{column}__valid"]
            if not flag.isin(["True", "False"]).all() or not flag.eq("True").eq(features[column].notna()).all():
                raise ValueError("Saved feature validity contradicts model inputs")
            valid = flag.eq("True")
            if not quality[f"{column}__reason"].eq("available").eq(valid).all():
                raise ValueError("Saved feature reasons contradict validity")
            available = _time_column(quality[f"{column}__available_at"], missing=True)
            if family.uses_measurements and available.loc[valid].isna().any():
                raise ValueError("Measurement features require availability evidence")
            if (valid & available.gt(raw["forecast_issue_time_utc"])).any():
                raise ValueError("Saved feature information was unavailable at issue time")
    for column in ("target_source_present", "target_available", "features_available", "complete_example", "training_example_eligible", "scoring_reference_available"):
        if not raw[column].isin(["True", "False"]).all():
            raise ValueError(f"Invalid boolean example mask: {column}")
        raw[column] = raw[column].eq("True")
    input_available = features.notna().all(axis=1)
    if not raw["features_available"].eq(input_available).all():
        raise ValueError("Example feature-availability mask contradicts X")
    missing_count = pd.to_numeric(raw["missing_feature_count"], errors="raise")
    if not missing_count.eq(features.isna().sum(axis=1)).all():
        raise ValueError("Example missing-feature count contradicts X")
    permitted = splits.isin(["train", evaluation_split])
    labels = pd.to_numeric(raw.loc[permitted, "target_consumption"].replace("", None), errors="raise").astype("float64")
    validate_numeric(labels)
    target_available = labels.notna()
    for column in ("target_available", "scoring_reference_available"):
        if not raw.loc[permitted, column].eq(target_available).all():
            raise ValueError("Reference availability masks contradict observed labels")
    if (target_available & ~raw.loc[permitted, "target_source_present"]).any():
        raise ValueError("Observed labels require source observations")
    complete = target_available & input_available.loc[permitted]
    if not raw.loc[permitted, "complete_example"].eq(complete).all() or not raw.loc[permitted, "training_example_eligible"].eq(complete & splits.loc[permitted].eq("train")).all():
        raise ValueError("Example eligibility masks contradict labels or split")
    metadata = raw.drop(columns="target_consumption")
    return BenchmarkDataset(features, labels, metadata, windows, evaluation_split, delay, expected_manifest_sha256, report)