"""Run standalone cleaning against one normalized hourly Statnett run."""

import argparse
from collections.abc import Sequence
from pathlib import Path
import sys

from oslo_energy.pipeline.cleaning import run_cleaning
from oslo_energy.transformation.cleaning import POLICY_REGISTRY


def main(argv: Sequence[str] | None = None) -> int:
    """Return zero for completed runs, including explicit source-quality warnings."""
    parser = argparse.ArgumentParser(description="Clean a normalized hourly run without API or database access.")
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("data/cleaned/statnett"))
    parser.add_argument("--policy", choices=sorted(POLICY_REGISTRY), default="preserve_and_flag")
    args = parser.parse_args(argv)
    try:
        result = run_cleaning(args.input_dir, output_dir=args.output_dir, policy_id=args.policy)
    except (OSError, ValueError, TypeError, OverflowError) as exc:
        print(f"Cleaning failed: {exc}", file=sys.stderr)
        return 1
    print(f"Cleaning {result.report['status']}: {result.report['counts']['output_observations']} observations")
    print(f"Saved cleaning run to {result.output_dir}")
    for warning in result.report["warnings"]:
        print(f"Warning: {warning}")
    print("Training readiness blocked; cleaning does not certify source accuracy or forecasting eligibility.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())