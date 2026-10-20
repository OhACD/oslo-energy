"""Plots consume evaluation evidence, never fit estimators or alter scoring."""

from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class PlotContext:
    metrics: dict
    predictions: pd.DataFrame
    title: str


class BenchmarkPlot(ABC):
    plot_id: str
    version = "1"
    filename: str

    def render(self, context: PlotContext, output_dir: Path) -> Path:
        if Path(self.filename).name != self.filename or not self.filename.endswith(".png"):
            raise ValueError("Plot filenames must be plain PNG artifact names")
        path = output_dir / self.filename
        if path.exists():
            raise FileExistsError(path)
        isolated = replace(context, metrics=deepcopy(context.metrics), predictions=context.predictions.copy(deep=True))
        self._render(isolated, path)
        if not path.is_file() or not path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("Plot renderer did not produce a PNG artifact")
        return path

    @abstractmethod
    def _render(self, context: PlotContext, output_path: Path) -> None:
        """Render derived evidence without changing the benchmark reference or metrics."""