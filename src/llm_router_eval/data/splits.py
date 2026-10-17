"""Family-separated stratification. No response, label or model-result input."""

from collections import Counter, defaultdict
from typing import Literal

from pydantic import Field

from .corpus import (
    COUNTS,
    Closed,
    Dataset,
    DatasetAdmission,
    Digest,
    Identifier,
    dataset_bytes,
    digest,
    load_dataset,
)


class SplitManifest(Closed):
    schema_version: Literal[1] = 1
    seed: int = 20261014
    dataset_sha256: Digest
    dev: list[Identifier] = Field(min_length=1)
    heldout: list[Identifier] = Field(min_length=1)
    families_sha256: Digest


def split_families(dataset: Dataset, *, seed: int = 20261014) -> SplitManifest:
    dataset = Dataset.model_validate(dataset.model_dump(mode="json"))
    if type(seed) is not int or seed < 0:
        raise ValueError("split_seed")
    families = defaultdict(list)
    for task in dataset.tasks:
        families[task.family_id].append(task)
    if any(len({t.kind for t in tasks}) != 1 for tasks in families.values()):
        raise ValueError("cross_kind_family")
    dev = []
    for kind, count in sorted(Counter(t.kind for t in dataset.tasks).items()):
        if count % 2:
            raise ValueError("uneven_kind")
        ordered = sorted(
            (name for name, ts in families.items() if ts[0].kind == kind),
            key=lambda name: digest([seed, kind, name]),
        )
        # Exact subset sum, deterministic tie choice; never split a family to hit a count.
        reachable = {0: ()}
        for name in ordered:
            size = len(families[name])
            for total, chosen in list(reachable.items()):
                if total + size <= count // 2:
                    reachable.setdefault(total + size, (*chosen, name))
        if count // 2 not in reachable:
            raise ValueError("family_split_impossible")
        dev.extend(t.task_id for name in reachable[count // 2] for t in families[name])
    import hashlib

    return SplitManifest(
        dataset_sha256=hashlib.sha256(dataset_bytes(dataset)).hexdigest(),
        seed=seed,
        dev=sorted(dev),
        heldout=sorted(t.task_id for t in dataset.tasks if t.task_id not in dev),
        families_sha256=digest(sorted([t.task_id, t.kind, t.family_id] for t in dataset.tasks)),
    )


def validate_split(dataset: Dataset, manifest: SplitManifest) -> None:
    expected = split_families(dataset, seed=manifest.seed)
    if manifest != expected:
        raise ValueError("split_binding")


def validate_benchmark(
    dataset: Dataset,
    manifest: SplitManifest,
    *,
    admission: DatasetAdmission,
    source_payload: bytes | None = None,
) -> None:
    if dataset.evidence_status != "frozen_corpus":
        raise ValueError("implementation_not_benchmark")
    admitted = load_dataset(
        dataset_bytes(dataset) if source_payload is None else source_payload,
        admission=admission,
    )
    if admitted != dataset:
        raise ValueError("benchmark_dataset_binding")
    dataset = admitted
    if manifest.seed != 20261014:
        raise ValueError("benchmark_split_seed")
    if dict(Counter(t.kind for t in dataset.tasks)) != COUNTS:
        raise ValueError("benchmark_mix")
    if any(s.role == "implementation_only" for s in dataset.sources):
        raise ValueError("benchmark_provenance")
    validate_split(dataset, manifest)
    if len(manifest.dev) != len(manifest.heldout) or len(manifest.dev) != 150:
        raise ValueError("benchmark_split")
