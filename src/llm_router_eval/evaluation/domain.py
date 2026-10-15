"""Private S3 labels and public prompt ports for the unchanged canonical S2 engine."""

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from ..data.corpus import load_dataset
from ..data.splits import split_families, validate_benchmark, validate_split
from ..shared.callledger.models import Digest, Identifier, WireModel, canonical_digest
from ..shared.evalkit.identity import canonical_bytes, ordered_ids_digest
from ..shared.evalkit.models import (
    GoldRecord,
    Manifest,
    MetricSpec,
    Outcome,
    Prediction,
    PublicMethodInput,
    PublicProjectionPolicy,
    SplitEntry,
)
from ..shared.evalkit.runner import SourceView
from ..shared.routing.scoring.deterministic import CandidateAnswer, ScoringLabel, score


class ReferenceAdmission(WireModel):
    """Actual caller-owned reference origin/provenance; dataset flags cannot declare humans."""

    receipt_sha256: Digest
    dataset_sha256: Digest
    label_sources: dict[Identifier, Literal["human", "human_corrected", "agent_provisional"]]
    provenance_refs: dict[Identifier, Identifier]
    independent_seal_sha256: Digest | None
    dev_config_sha256: Digest | None


class AnswerPayload(WireModel):
    candidate_status: Literal["ok", "failed"]
    selected_tier: Literal["code", "cheap", "frontier"]


def public_policy(method_configs, *, alias, origin):
    values = {
        "dataset_alias": (alias,),
        "split": ("dev", "heldout"),
        "method": tuple(method_configs),
        "config_ref": tuple(method_configs.values()),
        "metric": ("deterministic_accuracy",),
        "statistic_version": ("anchored-deterministic-assigned-v1",),
        "null_reason": ("zero_support", "zero_denominator", "not_run"),
        "evidence_origin": (origin, "not_run"),
    }
    return PublicProjectionPolicy(
        mode="aggregate_only",
        allowed_dimensions=tuple(values),
        allowed_value_sets=values,
        allowed_numeric_metrics=(
            "value",
            "numerator",
            "denominator",
            "corpus_N",
            "eligible_N",
            "answered_N",
            "comparable_N",
            "error_N",
            "abstention_N",
            "stale_N",
            "low",
            "high",
        ),
        synthetic_example_refs=(),
    )


@dataclass(frozen=True)
class EvaluationView:
    manifest: Manifest
    records: tuple[GoldRecord, ...] = field(repr=False)
    public_inputs: object = field(repr=False)
    evidence_status: str
    source_payload_sha256: str

    def split_records(self, split):
        entry = next((s for s in self.manifest.splits if s.split == split), None)
        if entry is None:
            raise ValueError("evaluation_split")
        indexed = {r.item_id: r for r in self.records}
        retained = tuple(indexed[i] for i in entry.ordered_item_ids)
        data = b"".join(canonical_bytes(r.model_dump(mode="json")) + b"\n" for r in retained)
        if hashlib.sha256(data).hexdigest() != entry.items_sha256:
            raise ValueError("evaluation_frozen_records_changed")
        return tuple(
            GoldRecord.model_validate_json(canonical_bytes(r.model_dump(mode="json")))
            for r in retained
        )

    @property
    def manifest_sha256(self):
        return hashlib.sha256(canonical_bytes(self.manifest.model_dump(mode="json"))).hexdigest()


def prepare_view(
    dataset_payload: bytes,
    *,
    frozen_at,
    method_configs,
    origin="synthetic",
    all_synthetic_dev=False,
    split_manifest=None,
    dataset_admission=None,
    reference_admission=None,
    trusted_reference_receipts=None,
):
    dataset = load_dataset(dataset_payload, admission=dataset_admission)
    raw_sha = hashlib.sha256(dataset_payload).hexdigest()
    reference = None
    if dataset.evidence_status == "frozen_corpus":
        if reference_admission is None:
            raise ValueError("reference_admission_required")
        reference = ReferenceAdmission.model_validate(reference_admission.model_dump(mode="json"))
        trusted = trusted_reference_receipts or {}
        ids = {t.task_id for t in dataset.tasks}
        if (
            trusted.get(reference.receipt_sha256)
            != canonical_digest(reference.model_dump(mode="json"))
            or reference.dataset_sha256 != raw_sha
            or set(reference.label_sources) != ids
            or set(reference.provenance_refs) != ids
        ):
            raise ValueError("reference_origin_admission")
        if all_synthetic_dev:
            raise ValueError("synthetic_population_only")
    elif reference_admission is not None:
        raise ValueError("synthetic_reference_cannot_promote")
    if all_synthetic_dev:
        # This is a declared ten-control DEV population, with zero heldout items.
        # Seeded ordering uses the existing family-order hash rule, not random inference.
        dev = tuple(
            t.task_id
            for t in sorted(
                dataset.tasks, key=lambda t: canonical_digest([20261014, t.family_id, t.task_id])
            )
        )
        heldout = ()
        seed = 20261014
    else:
        partition = split_manifest or split_families(dataset)
        validate_split(dataset, partition)
        if dataset.evidence_status == "frozen_corpus":
            validate_benchmark(dataset, partition, admission=dataset_admission)
        dev, heldout = tuple(partition.dev), tuple(partition.heldout)
        seed = partition.seed
    split_of = {i: "dev" for i in dev} | {i: "heldout" for i in heldout}
    records = []
    inputs = {}
    groups = defaultdict(list)
    for task in dataset.tasks:
        payload = task.routing_view()
        inputs[task.task_id] = MappingProxyType(payload)
        groups[task.family_id].append(task.task_id)
        records.append(
            GoldRecord(
                item_id=task.task_id,
                dataset="authored-controls" if reference is None else "admitted-corpus",
                group_id=task.family_id,
                split=split_of[task.task_id],
                label_version="anchored-v1",
                source_ref=dict(
                    handle=task.source_id + ":" + task.source_row_id, sha256=task.row_sha256
                ),
                input_sha256=canonical_digest(payload),
                label_source="synthetic"
                if reference is None
                else reference.label_sources[task.task_id],
                label_provenance_ref=None
                if reference is None
                else reference.provenance_refs[task.task_id],
                domain_kind="s3-answer-v1",
                label_payload=dict(
                    kind="domain_labels",
                    payload=dict(
                        kind=task.kind, answer=task.scoring.answer, rubric=task.scoring.rubric
                    ),
                ),
            )
        )
    indexed = {r.item_id: r for r in records}
    splits = []
    for name, ids in [("dev", dev), ("heldout", heldout)]:
        retained = tuple(indexed[i] for i in ids)
        data = b"".join(canonical_bytes(r.model_dump(mode="json")) + b"\n" for r in retained)
        splits.append(
            SplitEntry(
                split=name,
                items_file="derived-memory/" + name + ".jsonl",
                item_count=len(ids),
                ordered_item_ids=ids,
                items_sha256=hashlib.sha256(data).hexdigest(),
                ordered_ids_sha256=ordered_ids_digest(ids),
                group_ids=tuple(sorted({r.group_id for r in retained})),
            )
        )
    refs = {}
    for name, value in [
        ("heldout_seal", None if reference is None else reference.independent_seal_sha256),
        ("dev_config_lock", None if reference is None else reference.dev_config_sha256),
    ]:
        refs[name] = dict(
            status="not_available" if value is None else "available", ref=name, sha256=value
        )
    alias = "authored-controls" if reference is None else "admitted-corpus"
    manifest = Manifest(
        manifest_id="s3-" + alias,
        parent_manifest_sha256=None,
        split_seed=seed,
        partition_provenance="seeded",
        frozen_at=frozen_at,
        scorer_version="anchored-v1",
        comparator_config=dict(
            status="available",
            ref="declared-method-configs",
            sha256=canonical_digest(method_configs),
        ),
        domain_adapter_ref=dict(
            status="available",
            ref="installed-s3-domain-source",
            sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        ),
        experiment_refs=refs,
        splits=tuple(splits),
        group_split_map={g: split_of[ids[0]] for g, ids in groups.items()},
        group_members={g: tuple(ids) for g, ids in groups.items()},
        source_artifacts=(
            dict(
                namespace="s3-input-dataset-bytes",
                status="available",
                sha256=raw_sha,
                count=len(records),
                member_ids=tuple(r.item_id for r in records),
            ),
        ),
        publication_policy=public_policy(method_configs, alias=alias, origin=origin),
        run_history_ref="s3-fixed-history",
    )
    return EvaluationView(
        manifest, tuple(records), MappingProxyType(inputs), dataset.evidence_status, raw_sha
    )


class AnswerAdapter:
    """Scoring-only label access. Never passed into matrix builders or policy contexts."""

    def __init__(self, projection_policy):
        self.projection_policy = projection_policy

    @staticmethod
    def label(gold):
        if gold.domain_kind != "s3-answer-v1" or gold.label_payload.kind != "domain_labels":
            raise ValueError("domain_gold")
        payload = gold.label_payload.payload
        if set(payload) != {"kind", "answer", "rubric"}:
            raise ValueError("domain_gold")
        return ScoringLabel(prompt_id=gold.item_id, family_id=gold.group_id, **payload)

    def validate_gold(self, gold):
        self.label(gold)

    def to_public_input(self, gold, source_view: SourceView):
        value = source_view.public_payload
        if set(value) != {"task_id", "prompt"} or value["task_id"] != gold.item_id:
            raise ValueError("public_prompt_projection")
        return PublicMethodInput(
            item_id=gold.item_id,
            public_payload=dict(value),
            input_sha256=gold.input_sha256,
            public_config_refs=(),
        )

    def validate_prediction(self, prediction: Prediction):
        payload = AnswerPayload.model_validate(prediction.domain_payload)
        if prediction.human_effective_output is not None:
            raise ValueError("machine_prediction_required")
        if prediction.status == "ok" and (
            payload.candidate_status != "ok"
            or type(prediction.machine_output) is not str
            or not prediction.machine_output.strip()
        ):
            raise ValueError("domain_prediction")

    def to_outcomes(self, machine: Prediction, gold, policy):
        label = self.label(gold)
        candidate = CandidateAnswer(
            prompt_id=gold.item_id,
            status="ok" if machine.status == "ok" else "provider_error",
            text=machine.machine_output
            if machine.status == "ok" and type(machine.machine_output) is str
            else None,
        )
        scored = score(label, candidate)
        eligible = label.kind != "rubric"
        return (
            Outcome.model_validate(
                dict(
                    item_id=gold.item_id,
                    group_id=gold.group_id,
                    dataset=gold.dataset,
                    split=gold.split,
                    method=machine.method,
                    field=None,
                    **{"class": None},
                    eligibility="eligible" if eligible else "unlabelled",
                    status=machine.status,
                    correct=scored.passed if eligible else None,
                    predicted=machine.machine_output,
                    expected=label.answer if eligible else None,
                    human_touched_prediction=False,
                    evidence_origin=machine.origin,
                    issues=tuple((*machine.issues, "scoring:" + scored.reason)),
                    ledger_refs=machine.ledger_refs,
                )
            ),
        )

    @staticmethod
    def metric_specs():
        return (
            MetricSpec(
                metric="deterministic_accuracy",
                statistic_version="anchored-deterministic-assigned-v1",
                population="as_frozen",
                denominator_policy="all-assigned-deterministic",
                zero_denominator_reason="zero_support",
                averaging="none",
            ),
        )

    def public_projection_policy(self):
        return self.projection_policy
