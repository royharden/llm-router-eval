"""Opt-in fixed regeneration from original facts; no response editing or replay bundle."""

from typing import Literal, Protocol

from ..callledger.models import Digest, WireModel, canonical_digest
from .models import GenerateRequest, ImagePart

BUILDER_VERSION = "regenerate_original_v1"
REGENERATION_INSTRUCTION = (
    "Regenerate the answer using only the original source facts and supplied constraints. "
    "Obey the supplied JSON schema and content constraints. Do not invent unsupported facts. "
    "Return only the requested answer."
)


class GenerationRepairPolicy(WireModel):
    schema_version: Literal[1]
    mode: Literal["regenerate_original_v1"]
    base_config_sha256: Digest
    content_validator_revision_sha256: Digest | None = None

    @property
    def bound_config_sha256(self) -> str:
        return canonical_digest(
            {
                "policy": self.model_dump(mode="json"),
                "builder_version": BUILDER_VERSION,
                "instruction_sha256": canonical_digest(REGENERATION_INSTRUCTION),
            }
        )


class ContentValidator(Protocol):
    """Trusted, pure, bounded caller code. Its declared revision binds the root config."""

    revision_sha256: str

    def validate(self, parsed_json: object) -> bool: ...


def build_generation_repair_request(original: GenerateRequest, policy: GenerationRepairPolicy):
    GenerationRepairPolicy.model_validate(policy.model_dump(mode="json"))
    if not isinstance(original, GenerateRequest):
        raise ValueError("unsupported_generation_repair")
    original = GenerateRequest.model_validate(original.model_dump(mode="json", by_alias=True))
    if (
        original.stream
        or original.output_schema is None
        or any(
            isinstance(part, ImagePart) for message in original.messages for part in message.content
        )
    ):
        raise ValueError("unsupported_generation_repair")
    payload = original.model_dump(mode="json", by_alias=True)
    payload["messages"].insert(
        0, {"role": "system", "content": [{"type": "text", "text": REGENERATION_INSTRUCTION}]}
    )
    return GenerateRequest.model_validate(payload)
