"""Generate basic causal features from one versioned research configuration."""

import argparse
from collections.abc import Sequence
from pathlib import Path
import sys

from oslo_energy.pipeline.features import run_features


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate causal lag/calendar features without training a model.")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("data/features/statnett"))
    args = parser.parse_args(argv)
    try:
        result = run_features(args.config, output_dir=args.output_dir)
    except (OSError, ValueError, TypeError, KeyError, OverflowError) as exc:
        print(f"Feature generation failed: {exc}", file=sys.stderr)
        return 1
    for name, counts in result.report["counts_by_split"].items():
        print(f"{name}: {counts['intended_examples']} intended; {counts['features_available']} input-ready; "
              f"{counts['complete_examples']} complete feature/label examples")
    print(f"Saved feature run to {result.output_dir}")
    print("training_ready=false; publication-time assumptions and research review remain unresolved. No model trained.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())