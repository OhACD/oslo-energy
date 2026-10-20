"""Audit explicit research intervals without changing the cleaned dataset."""

import argparse
from collections.abc import Sequence
from datetime import date
from pathlib import Path
import sys

from oslo_energy.pipeline.research_audit import run_research_audit
from oslo_energy.pipeline.research_config import load_research_config
from oslo_energy.transformation.research_audit import ResearchWindow


def parse_window(value: str) -> ResearchWindow:
    """Parse name:inclusive-from-date:inclusive-to-date in Europe/Oslo."""
    try:
        name, start, end = value.split(":")
        from_date, to_date = date.fromisoformat(start), date.fromisoformat(end)
        if from_date.isoformat() != start or to_date.isoformat() != end:
            raise ValueError(value)
        return ResearchWindow(name, from_date, to_date)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("expected name:YYYY-MM-DD:YYYY-MM-DD with ordered dates") from exc


def main(argv: Sequence[str] | None = None) -> int:
    """Return zero for a completed descriptive audit, not research approval."""
    parser = argparse.ArgumentParser(description="Audit a cleaned hourly snapshot for explicit research windows.")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--input-dir", type=Path)
    parser.add_argument("--window", action="append", type=parse_window)
    parser.add_argument("--output-dir", type=Path, default=Path("data/audits/statnett"))
    parser.add_argument("--top-changes", type=int, default=10)
    parser.add_argument("--absolute-change-threshold", type=float)
    args = parser.parse_args(argv)
    if args.config and (args.input_dir or args.window):
        parser.error("--config cannot be mixed with --input-dir or --window")
    if not args.config and (not args.input_dir or not args.window):
        parser.error("provide --config or both --input-dir and --window")
    try:
        config = load_research_config(args.config) if args.config else None
        result = run_research_audit(
            config.cleaned_dir if config else args.input_dir,
            windows=config.windows if config else args.window, output_dir=args.output_dir,
            top_changes=args.top_changes, absolute_change_threshold=args.absolute_change_threshold,
            config_path=args.config,
        )
    except (OSError, ValueError, TypeError, KeyError, OverflowError) as exc:
        print(f"Research audit failed: {exc}", file=sys.stderr)
        return 1
    for window in result.report["windows"]:
        print(
            f"{window['name']}: {window['present_hour_count']}/{window['expected_hour_count']} hours present; "
            f"{window['consumption_null_count']} null consumption; "
            f"longest unavailable stretch {window['longest_unavailable_consumption_hours']} hours"
        )
    print(f"Saved research audit to {result.output_dir}")
    print("Research eligibility pending review; training_ready=false. No source-accuracy certification.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())