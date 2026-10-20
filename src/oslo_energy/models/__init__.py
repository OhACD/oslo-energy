"""Forecast adapters; optional learned-model dependencies are loaded on demand."""

from oslo_energy.models.base import ModelAdapter
from oslo_energy.models.contracts import ForecastBatch, PredictionResult, TrainingBatch
from oslo_energy.models.registry import MODEL_REGISTRY, get_model, load_model


__all__ = ["ForecastBatch", "MODEL_REGISTRY", "ModelAdapter", "PredictionResult", "TrainingBatch", "get_model", "load_model"]