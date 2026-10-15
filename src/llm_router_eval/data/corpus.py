"""Closed local inputs. Routing receives a separately constructed public view."""

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..shared.callledger.models import decode_wire_json

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]
Kind = Literal["gsm8k", "mmlu", "code", "exact", "rubric"]
COUNTS = {"gsm8k": 100, "mmlu": 100, "code": 40, "exact": 20, "rubric": 40}


def canonical_bytes(value) -> bytes:
    return (
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        )
        + "\n"
    ).encode("utf-8")


def digest(value) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)


class ScoringData(Closed):
    answer: Annotated[str, Field(max_length=8000)] | None = None
    rubric: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=2000)]], Field(max_length=8)
    ] = Field(default_factory=list)


class Task(Closed):
    task_id: Identifier
    family_id: Identifier
    kind: Kind
    prompt: Annotated[str, Field(min_length=1, max_length=8000)]
    source_id: Identifier
    source_row_id: Identifier
    row_sha256: Digest
    scoring: ScoringData
    analysis_difficulty: Literal["easy", "medium", "hard"] | None = None

    @model_validator(mode="after")
    def valid_task(self):
        if len(self.prompt.encode("utf-8")) > 8000 or not self.prompt.strip():
            raise ValueError("prompt_bytes")
        if self.kind == "rubric":
            if not self.scoring.rubric or self.scoring.answer is not None:
                raise ValueError("rubric_scoring")
        elif self.scoring.answer is None or self.scoring.rubric:
            raise ValueError("exact_scoring")
        if self.kind == "mmlu" and self.scoring.answer not in {"A", "B", "C", "D"}:
            raise ValueError("option_scoring")
        core = self.model_dump(exclude={"row_sha256"}, mode="json")
        if digest(core) != self.row_sha256:
            raise ValueError("row_digest")
        return self

    def routing_view(self) -> dict[str, str]:
        # Explicit projection: new private fields never silently enter routing context.
        Task.model_validate(self.model_dump(mode="json"))
        return {"task_id": self.task_id, "prompt": self.prompt}


class SourceProvenance(Closed):
    source_id: Identifier
    source_revision: Annotated[str, Field(min_length=1, max_length=256)]
    source_sha256: Digest
    licence_id: Annotated[str, Field(min_length=1, max_length=128)]
    rights_receipt_sha256: Digest | None
    author_id: Identifier
    role: Literal["implementation_only", "benchmark_source", "independent_original"]

    @model_validator(mode="after")
    def rights_required(self):
        if self.role != "implementation_only" and self.rights_receipt_sha256 is None:
            raise ValueError("rights_receipt_required")
        return self


class Dataset(Closed):
    schema_version: Literal[1]
    evidence_status: Literal["synthetic_implementation", "frozen_corpus"]
    sources: Annotated[list[SourceProvenance], Field(min_length=1, max_length=300)]
    tasks: Annotated[list[Task], Field(min_length=1, max_length=300)]

    @model_validator(mode="after")
    def unique_and_bound(self):
        sources = {source.source_id: source for source in self.sources}
        if len(sources) != len(self.sources):
            raise ValueError("duplicate_source")
        if len({t.task_id for t in self.tasks}) != len(self.tasks):
            raise ValueError("duplicate_task")
        if len({t.prompt.strip() for t in self.tasks}) != len(self.tasks):
            raise ValueError("duplicate_prompt")
        rows = set()
        for task in self.tasks:
            if task.source_id not in sources:
                raise ValueError("missing_source")
            key = (task.source_id, task.source_row_id)
            if key in rows:
                raise ValueError("duplicate_source_row")
            rows.add(key)
        if set(sources) != {t.source_id for t in self.tasks}:
            raise ValueError("unused_source")
        if self.evidence_status == "synthetic_implementation" and any(
            s.role != "implementation_only" for s in self.sources
        ):
            raise ValueError("synthetic_role")
        return self


class DatasetAdmission(Closed):
    """Trusted caller's exact frozen input grant, never loaded from the data itself."""

    dataset_sha256: Digest
    source_grants: dict[Identifier, SourceProvenance]
    freeze_receipt_sha256: Digest
    independent_author_ids: Annotated[list[Identifier], Field(min_length=1)]
    excluded_tuner_ids: Annotated[list[Identifier], Field(min_length=1)]


def dataset_bytes(dataset: Dataset) -> bytes:
    checked = Dataset.model_validate(dataset.model_dump(mode="json"))
    return canonical_bytes(checked.model_dump(mode="json"))


def load_dataset(payload: bytes, *, admission: DatasetAdmission | None = None) -> Dataset:
    if len(payload) > 8_000_000:
        raise ValueError("dataset_bytes")
    dataset = Dataset.model_validate(decode_wire_json(payload.decode("utf-8")))
    if dataset.evidence_status == "frozen_corpus":
        if admission is None:
            raise ValueError("frozen_admission_required")
        grant = DatasetAdmission.model_validate(admission.model_dump(mode="json"))
        if hashlib.sha256(payload).hexdigest() != grant.dataset_sha256:
            raise ValueError("frozen_digest")
        if {s.source_id: s for s in dataset.sources} != grant.source_grants:
            raise ValueError("source_grant")
        authors, tuners = set(grant.independent_author_ids), set(grant.excluded_tuner_ids)
        if authors & tuners or not {s.author_id for s in dataset.sources} <= authors:
            raise ValueError("independent_authorship")
        if any(s.role == "implementation_only" for s in dataset.sources):
            raise ValueError("implementation_not_corpus")
    return dataset
