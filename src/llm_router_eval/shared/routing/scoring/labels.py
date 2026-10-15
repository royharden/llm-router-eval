"""Blank blinded labeling templates and exact trusted admission; no guessed labels."""

import hashlib
import json
from typing import Literal

from pydantic import model_validator

from ...callledger.models import Digest, Identifier, WireModel, canonical_digest, decode_wire_json


class PairSpec(WireModel):
    pair_id: Identifier
    prompt_id: Identifier
    candidate_source: Literal["cheap", "frontier", "corruption"]
    response_sha256: Digest


class HumanLabel(WireModel):
    pair_id: Identifier
    passed: bool


class HumanLabelFile(WireModel):
    schema_version: Literal[1]
    evidence_kind: Literal["synthetic_implementation", "blinded_human"]
    human_id: Identifier
    blind_manifest_sha256: Digest
    labels: list[HumanLabel]

    @model_validator(mode="after")
    def unique_labels(self):
        if not self.labels or len({v.pair_id for v in self.labels}) != len(self.labels):
            raise ValueError("label_ids")
        return self


class HumanAdmission(WireModel):
    label_file_sha256: Digest
    blind_manifest_sha256: Digest
    pair_manifest_sha256: Digest
    human_id: Identifier
    owner_receipt_sha256: Digest
    expected_pair_ids: list[Identifier]
    excluded_agent_ids: list[Identifier]


def canonical_json(value) -> bytes:
    return (
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":")
        )
        + "\n"
    ).encode()


def blank_template(pairs: list[PairSpec]) -> bytes:
    checked = [PairSpec.model_validate(p.model_dump(mode="json")) for p in pairs]
    if not checked or len({p.pair_id for p in checked}) != len(checked):
        raise ValueError("pair_ids")
    # No judge outcomes, source tier/cohort, prompt body, human name or filled pass/fail value.
    manifest = [
        dict(pair_id=p.pair_id, prompt_id=p.prompt_id, response_sha256=p.response_sha256)
        for p in checked
    ]
    return canonical_json(
        dict(
            schema_version=1,
            kind="blank_label_template",
            blind_manifest_sha256=canonical_digest(manifest),
            labels=[dict(pair_id=p.pair_id, passed=None) for p in checked],
        )
    )


def admit_human_labels(
    payload: bytes, *, admission: HumanAdmission, pairs: list[PairSpec]
) -> HumanLabelFile:
    if len(payload) > 128_000:
        raise ValueError("label_file_bytes")
    grant = HumanAdmission.model_validate(admission.model_dump(mode="json"))
    checked = [PairSpec.model_validate(p.model_dump(mode="json")) for p in pairs]
    if not checked or len({p.pair_id for p in checked}) != len(checked):
        raise ValueError("pair_ids")
    value = HumanLabelFile.model_validate(decode_wire_json(payload.decode("utf-8")))
    expected = json.loads(blank_template(checked))["blind_manifest_sha256"]
    if canonical_digest([p.model_dump(mode="json") for p in checked]) != grant.pair_manifest_sha256:
        raise ValueError("pair_manifest")
    if (
        value.evidence_kind != "blinded_human"
        or hashlib.sha256(payload).hexdigest() != grant.label_file_sha256
    ):
        raise ValueError("human_input_admission")
    if value.human_id != grant.human_id or value.human_id in grant.excluded_agent_ids:
        raise ValueError("human_identity_admission")
    if (
        value.blind_manifest_sha256 != grant.blind_manifest_sha256
        or value.blind_manifest_sha256 != expected
    ):
        raise ValueError("blind_manifest")
    ids = {p.pair_id for p in checked}
    if (
        len(set(grant.expected_pair_ids)) != len(grant.expected_pair_ids)
        or set(grant.expected_pair_ids) != ids
        or {v.pair_id for v in value.labels} != ids
    ):
        raise ValueError("human_label_coverage")
    return value
