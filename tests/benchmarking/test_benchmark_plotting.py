"""Render actual metric values without hiding missing predictions or failed methods."""

import pandas as pd
import pytest

from oslo_energy.benchmarking.evaluation import evaluate_predictions
from oslo_energy.benchmarking.plotting import PlotContext, get_plot, render_plots, write_comparison_chart
from oslo_energy.models.contracts import PredictionResult


def metrics(failed=False):
    index = pd.date_range("2025-01-01", periods=3, freq="h", tz="UTC")
    reference = pd.Series([1.0, 2.0, 3.0], index=index)
    baseline = PredictionResult(reference + 1, pd.Series("predicted", index=index))
    values = pd.Series([1.0, 2.0, float("nan")], index=index)
    status = pd.Series(["predicted", "predicted", "unavailable_input"], index=index)
    if failed:
        values[:] = float("nan")
        status[:] = "failed"
    return evaluate_predictions(reference, {"seasonal_naive": baseline, "linear_regression": PredictionResult(values, status)}, baselines=["seasonal_naive"])


@pytest.mark.parametrize("failed", [False, True])
def test_comparison_chart_renders_and_preserves_existing_files(tmp_path, failed):
    path = tmp_path / "comparison.png"
    write_comparison_chart(metrics(failed), path, title="Synthetic validation comparison")
    assert path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert path.stat().st_size > 10000
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        write_comparison_chart(metrics(failed), path, title="Must not replace")
    assert path.read_bytes() == original


def test_registered_plot_writes_inside_run_without_mutating_metrics(tmp_path):
    evidence = metrics()
    original = evidence["individual"]["seasonal_naive"]["errors"]["mae"]
    files = render_plots(["comparison"], PlotContext(evidence, pd.DataFrame(), "Synthetic experiment"), tmp_path)
    assert files == ["plots/benchmark_comparison.png"]
    assert (tmp_path / files[0]).is_file()
    assert evidence["individual"]["seasonal_naive"]["errors"]["mae"] == original
    assert get_plot("comparison").version == "1"
    with pytest.raises(ValueError, match="Unknown"):
        get_plot("arbitrary.module")
    with pytest.raises(ValueError, match="Duplicate"):
        render_plots(["comparison", "comparison"], PlotContext(evidence, pd.DataFrame(), "test"), tmp_path)


def test_new_plot_can_register_without_model_or_scoring_changes(tmp_path, monkeypatch):
    from oslo_energy.benchmarking.plotting import BenchmarkPlot
    from oslo_energy.benchmarking.plotting import registry

    class AlternatePlot(BenchmarkPlot):
        plot_id = "alternate"
        filename = "alternate.png"

        def _render(self, context, output_path):
            context.metrics["individual"]["seasonal_naive"]["errors"]["mae"] = 123
            write_comparison_chart(context.metrics, output_path, title=context.title)

    evidence = metrics()
    original = evidence["individual"]["seasonal_naive"]["errors"]["mae"]
    monkeypatch.setattr(registry, "PLOT_REGISTRY", {**registry.PLOT_REGISTRY, "alternate": AlternatePlot})
    assert registry.render_plots(["alternate"], PlotContext(evidence, pd.DataFrame(), "Extension test"), tmp_path) == ["plots/alternate.png"]
    assert evidence["individual"]["seasonal_naive"]["errors"]["mae"] == original