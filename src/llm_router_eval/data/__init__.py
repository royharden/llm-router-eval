"""Local data admission and seals; no corpus acquisition or benchmark execution."""

from .corpus import Dataset, Task, canonical_bytes, dataset_bytes, load_dataset
from .seals import CampaignJournal, ConfigurationSeal
from .splits import SplitManifest, split_families, validate_benchmark

__all__ = [
    "CampaignJournal",
    "ConfigurationSeal",
    "Dataset",
    "SplitManifest",
    "Task",
    "canonical_bytes",
    "dataset_bytes",
    "load_dataset",
    "split_families",
    "validate_benchmark",
]
