"""Create editable experiment source blueprints without executing research."""

import argparse
from pathlib import Path
import sys
from typing import Sequence

from oslo_energy.pipeline.blueprint import generate_experiment_blueprint
from oslo_energy.pipeline.run_research_audit import parse_window


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate feature/benchmark configs, a notebook controller, and README for a new experiment.")
    parser.add_argument("experiment_id", help="unique lowercase ID, e.g. 003_my_study_v1")
    parser.add_argument("--experiments-dir", type=Path, default=Path("experiments"))
    parser.add_argument("--cleaned-run", type=Path, help="verify and pin this cleaned manifest automatically")
    parser.add_argument("--window", action="append", type=parse_window, help="repeat for chronological train, validation and test windows")
    parser.add_argument("--lags", nargs="+", type=int, default=[1, 24, 168])
    parser.add_argument("--models", nargs="+", default=["seasonal_naive", "persistence", "linear_regression"])
    parser.add_argument("--plots", nargs="*", default=["comparison"])
    parser.add_argument("--publication-delay-hours", type=int, default=0)
    args = parser.parse_args(argv)
    try:
        result = generate_experiment_blueprint(
            args.experiment_id, experiments_dir=args.experiments_dir, cleaned_run=args.cleaned_run,
            windows=args.window, lags=args.lags, model_ids=args.models, plot_ids=args.plots,
            publication_delay_hours=args.publication_delay_hours,
        )
    except (OSError, ValueError, TypeError, KeyError, ImportError) as exc:
        print(f"Blueprint generation failed: {exc}", file=sys.stderr)
        return 1
    print(f"Created experiment blueprint: {result.output_dir}")
    if result.pending_fields:
        print("Complete these explicit settings before running:")
        for field in result.pending_fields:
            print(f"  {field}")
    else:
        print("Input references and windows are populated; review all settings before execution.")
    print("No data fetched, models fitted, or evaluation performed. Open experiment.ipynb or use run_experiment after review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())