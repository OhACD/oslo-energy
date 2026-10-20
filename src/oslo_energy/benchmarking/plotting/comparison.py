"""Common-timestamp errors, coverage, and paired skill for forecast comparisons."""

from pathlib import Path

from oslo_energy.benchmarking.plotting.contracts import BenchmarkPlot, PlotContext


def write_comparison_chart(metrics: dict, output_path: Path, *, title: str) -> None:
    """Compare shared-timestamp errors, coverage, and paired baseline-relative skill."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    if output_path.exists():
        raise FileExistsError(output_path)
    names = list(metrics["individual"])
    labels = {"seasonal_naive": "Seasonal naive (168 h)", "persistence": "Persistence (1 h)",
              "linear_regression": "Linear regression"}
    display = [labels.get(name, name) for name in names]
    figure = Figure(figsize=(15, max(9, 5 + len(names) * 0.9)), facecolor="white")
    FigureCanvasAgg(figure)
    axes = figure.subplots(2, 2)
    figure.subplots_adjust(left=0.16, right=0.97, bottom=0.20, top=0.85, hspace=0.7, wspace=0.65)
    figure.suptitle(title, x=0.16, y=0.97, ha="left", fontsize=17, fontweight="bold")
    figure.text(0.16, 0.91, "One-hour consumption forecasts | lower error is better | coverage retained separately", fontsize=10, color="#475569")
    colors = ["#087f8c", "#dd8a22", "#5068b8", "#a34e6d"]
    common = metrics["all_models_common"]
    for axis, key, label in ((axes[0, 0], "mae", "MAE"), (axes[0, 1], "rmse", "RMSE")):
        values = [common[name][key] for name in names]
        for position, value in enumerate(values):
            if value is not None:
                axis.barh(position, value, height=0.52, color=colors[position % len(colors)])
            axis.text(value if value is not None else 0, position,
                      f"  {value:,.2f}" if value is not None else " No shared scored timestamps",
                      va="center", fontsize=9)
        axis.set_yticks(range(len(names)), display)
        axis.invert_yaxis()
        axis.set_title(f"{label} on common timestamps (n={common[names[0]]['count']:,})", loc="left", fontsize=11)
        maximum = max((value for value in values if value is not None), default=0)
        axis.set_xlim(0, maximum * 1.35 if maximum else 1)
        axis.set_xlabel("Unverified provider units", fontsize=9)
        axis.grid(axis="x", color="#e5e7eb")
        axis.set_axisbelow(True)
    coverage = axes[1, 0]
    for offset, field, label, color in (
        (-0.17, "prediction_coverage_of_intended", "Predicted", "#087f8c"),
        (0.17, "scored_coverage_of_intended", "Scored", "#dd8a22"),
    ):
        for position, name in enumerate(names):
            value = metrics["individual"][name][field]
            percent = value * 100 if value is not None else 0
            coverage.barh(position + offset, percent, height=0.28, color=color,
                          label=label if position == 0 else None)
            coverage.text(percent + 1, position + offset,
                          f"{percent:.1f}%" if value is not None else "N/A", va="center", fontsize=8)
    coverage.set_yticks(range(len(names)), display)
    coverage.invert_yaxis()
    coverage.set_xlim(0, 116)
    coverage.set_title("Coverage of intended forecast timestamps", loc="left", fontsize=11)
    coverage.set_xlabel("Percent of intended forecasts", fontsize=9)
    coverage.legend(loc="lower left", bbox_to_anchor=(0, -0.48), frameon=False, ncol=2, fontsize=9)
    coverage.grid(axis="x", color="#e5e7eb")
    coverage.set_axisbelow(True)
    skill_axis = axes[1, 1]
    skill_axis.axis("off")
    baselines = list(metrics["paired_against_baselines"][names[0]])
    cells = []
    for name in names:
        row = [labels.get(name, name)]
        for baseline in baselines:
            paired = metrics["paired_against_baselines"][name][baseline]
            skill = paired["mae_skill"]
            row.append(f"{skill:+.1%}\n(n={paired['model']['count']:,})" if skill is not None else
                       f"N/A\n{paired['skill_status'].replace('_', ' ')}")
        cells.append(row)
    headers = ["Model", *[labels.get(name, name).replace(" (", "\n(") for name in baselines]]
    table = skill_axis.table(cellText=cells, colLabels=headers, cellLoc="center", bbox=[0, 0.05, 1, 0.85])
    table.auto_set_font_size(False)
    table.set_fontsize(8)
    for cell in table.get_celld().values():
        cell.set_edgecolor("#d1d5db")
    skill_axis.set_title("Paired MAE skill vs. each baseline", loc="left", fontsize=11)
    figure.text(0.16, 0.025,
                "Positive skill improves on the named baseline; paired sample counts may differ.\n"
                "Retrospective API-reference evaluation. Publication timing, revisions, and source accuracy remain unresolved.",
                fontsize=9, color="#475569")
    try:
        figure.savefig(output_path, dpi=160, bbox_inches="tight")
    finally:
        figure.clear()


class ComparisonPlot(BenchmarkPlot):
    plot_id = "comparison"
    filename = "benchmark_comparison.png"

    def _render(self, context: PlotContext, output_path: Path) -> None:
        write_comparison_chart(context.metrics, output_path, title=context.title)