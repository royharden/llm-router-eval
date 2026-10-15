"""Config seals and offline cell continuation records, not a benchmark executor."""

from typing import Literal

from pydantic import Field, model_validator

from .corpus import Closed, Digest, Identifier, digest


class ConfigurationSeal(Closed):
    schema_version: Literal[1] = 1
    dataset_sha256: Digest
    split_sha256: Digest
    models_sha256: Digest
    prices_sha256: Digest
    policies_sha256: Digest
    thresholds_sha256: Digest
    judge_sha256: Digest
    sources_sha256: Digest
    input_manifest_sha256: Digest

    def identity(self) -> str:
        checked = ConfigurationSeal.model_validate(self.model_dump(mode="json"))
        return digest(checked.model_dump(mode="json"))


class CellRecord(Closed):
    task_id: Identifier
    method_id: Identifier
    capture_sha256: Digest


class CampaignJournal(Closed):
    schema_version: Literal[1] = 1
    seal_sha256: Digest
    split: Literal["dev", "heldout"]
    assigned_ids: list[Identifier] = Field(min_length=1, max_length=300)
    method_ids: list[Identifier] = Field(min_length=1, max_length=32)
    completed: list[CellRecord] = Field(default_factory=list)

    @classmethod
    def start(cls, *, dataset, manifest, seal: ConfigurationSeal, split, method_ids):
        from .splits import validate_split

        validate_split(dataset, manifest)
        if seal.dataset_sha256 != manifest.dataset_sha256 or seal.split_sha256 != digest(
            manifest.model_dump(mode="json")
        ):
            raise ValueError("seal_input_binding")
        if split not in {"dev", "heldout"}:
            raise ValueError("split_name")
        return cls(
            seal_sha256=seal.identity(),
            split=split,
            assigned_ids=getattr(manifest, split),
            method_ids=method_ids,
        )

    @model_validator(mode="after")
    def validate_cells(self):
        if len(set(self.assigned_ids)) != len(self.assigned_ids):
            raise ValueError("duplicate_assigned")
        if len(set(self.method_ids)) != len(self.method_ids):
            raise ValueError("duplicate_method")
        seen = set()
        for cell in self.completed:
            key = (cell.task_id, cell.method_id)
            if (
                key in seen
                or cell.task_id not in self.assigned_ids
                or cell.method_id not in self.method_ids
            ):
                raise ValueError("cell_binding")
            seen.add(key)
        return self

    def append(self, record: CellRecord, *, seal: ConfigurationSeal) -> "CampaignJournal":
        checked = CampaignJournal.model_validate(self.model_dump(mode="json"))
        if checked.seal_sha256 != seal.identity():
            raise ValueError("configuration_changed")
        data = checked.model_dump(mode="json")
        data["completed"].append(record.model_dump(mode="json"))
        return CampaignJournal.model_validate(data)

    def missing(self) -> list[tuple[str, str]]:
        checked = CampaignJournal.model_validate(self.model_dump(mode="json"))
        done = {(c.task_id, c.method_id) for c in checked.completed}
        return [
            (task, method)
            for task in checked.assigned_ids
            for method in checked.method_ids
            if (task, method) not in done
        ]
