"""Explicit benchmark invocation; validation is default and final test is opt-in."""

import argparse
from collections.abc import Sequence
from pathlib import Path
import sys

from oslo_energy.ingestion.archive import Archive
from oslo_energy.pipeline.benchmark import run_benchmark


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare unfitted baselines and train-only ordinary linear regression.")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path, help="defaults to runs/ beside the benchmark configuration")
    parser.add_argument("--allow-test-evaluation", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run_benchmark(args.config, output_dir=args.output_dir, allow_test_evaluation=args.allow_test_evaluation)
    except (OSError, ValueError, TypeError, KeyError, OverflowError) as exc:
        print(f"Benchmark failed: {exc}", file=sys.stderr)
        return 1
    metrics = Archive(result.output_dir / "metrics.json").read()
    for name, values in metrics["individual"].items():
        print(f"{name}: {values['predicted']}/{values['intended']} predicted; {values['scored']} scored; "
              f"MAE={values['errors']['mae']}; RMSE={values['errors']['rmse']}")
    print(f"Saved benchmark to {result.output_dir}; training_ready=false; provisional retrospective results only.")
    return 1 if result.report["status"] == "completed_with_model_failures" else 0


if __name__ == "__main__":
    raise SystemExit(main())