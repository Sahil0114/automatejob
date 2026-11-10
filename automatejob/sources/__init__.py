"""Extensible job source adapters.

Each adapter fetches a list of postings from a public, documented job-board
API and normalises them into `Job` objects. New sources subclass `BaseSource`.
"""

from .ashby import AshbySource
from .base import BaseSource
from .greenhouse import GreenhouseSource
from .hn import HNSource
from .lever import LeverSource
from .simplifyjobs import SimplifyJobsSource
from .yc_waas import YCJobsSource

__all__ = ["BaseSource", "GreenhouseSource", "LeverSource", "AshbySource",
           "HNSource", "SimplifyJobsSource", "YCJobsSource"]

SOURCE_REGISTRY = {
    "greenhouse": GreenhouseSource,
    "lever": LeverSource,
    "ashby": AshbySource,
    "hn": HNSource,
    "simplifyjobs": SimplifyJobsSource,
    "yc_waas": YCJobsSource,
}


def build_source(source_type: str, token: str, name: str, http) -> BaseSource:
    """Instantiate a source adapter by type, raising a clear error if unknown."""
    cls = SOURCE_REGISTRY.get(source_type.lower())
    if cls is None:
        raise ValueError(
            f"unknown source type '{source_type}'. "
            f"Known types: {', '.join(sorted(SOURCE_REGISTRY))}"
        )
    return cls(token=token, name=name, http=http)
