"""Explicit plot selection shared by configuration and experiment publication."""

from pathlib import Path
from types import MappingProxyType

from oslo_energy.benchmarking.plotting.comparison import ComparisonPlot
from oslo_energy.benchmarking.plotting.contracts import PlotContext


PLOT_REGISTRY = MappingProxyType({ComparisonPlot.plot_id: ComparisonPlot})


def get_plot(plot_id: str):
    if plot_id not in PLOT_REGISTRY:
        raise ValueError(f"Unknown benchmark plot: {plot_id}")
    return PLOT_REGISTRY[plot_id]()


def render_plots(plot_ids: list[str], context: PlotContext, run_dir: Path) -> list[str]:
    if len(set(plot_ids)) != len(plot_ids):
        raise ValueError("Duplicate plot IDs")
    plots = [get_plot(plot_id) for plot_id in plot_ids]
    if len({plot.filename for plot in plots}) != len(plots):
        raise ValueError("Selected plots have conflicting artifact names")
    if not plots:
        return []
    output_dir = run_dir / "plots"
    output_dir.mkdir(exist_ok=False)
    return [str(plot.render(context, output_dir).relative_to(run_dir)) for plot in plots]