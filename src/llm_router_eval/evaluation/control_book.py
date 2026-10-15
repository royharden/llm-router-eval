"""Explicitly authored fixture matrix from public inputs and existing synthetic answer vectors.

No Dataset, scoring labels, rubrics or analysis difficulty are imported or read here.
"""

import hashlib
from dataclasses import dataclass

from ..shared.callledger.models import NormalizedUsage, canonical_digest, decode_wire_json
from ..shared.providers import FixtureIndex, ModelSupport, ProviderConfig
from ..shared.providers.adapters import Outcome
from ..shared.providers.captures import FixtureRecord, synthetic_capture
from ..shared.providers.models import TypedJudgmentResult
from ..shared.routing.code_tier import can_handle
from ..shared.routing.policies import SCORE_LEVELS
from .matrix import PublicInferenceConfig, build_matrix_spec


def control_config():
    def generation(model):
        return ModelSupport(
            generation=True,
            typed_judgment=False,
            structured_output=True,
            image_input=False,
            streaming=False,
            temperature=False,
            reasoning_efforts=["low"],
            reported_models=[model + "-reported"],
        )

    config = ProviderConfig(
        schema_version=1,
        flavor="openai_responses",
        endpoint_id="authored-offline-endpoint",
        revision_sha256=canonical_digest("authored-no-sdk-generation-v1"),
        models={name: generation(name) for name in ["fixture-cheap", "fixture-frontier"]},
    )
    typed = ProviderConfig(
        schema_version=1,
        flavor="typesafe",
        endpoint_id="authored-offline-typed-endpoint",
        revision_sha256=canonical_digest("authored-no-sdk-typesafe-v1"),
        models={
            "fixture-jev": ModelSupport(
                generation=False,
                typed_judgment=True,
                structured_output=False,
                image_input=False,
                streaming=False,
                temperature=False,
                reasoning_efforts=[],
                reported_models=["fixture-jev-reported"],
            )
        },
    )
    return PublicInferenceConfig(
        cheap_model="fixture-cheap",
        frontier_model="fixture-frontier",
        jev_model="fixture-jev",
        generation_provider=config,
        typed_provider=typed,
    )


@dataclass(frozen=True)
class ControlBook:
    specs: tuple
    records: tuple[FixtureRecord, ...]
    authored_vectors_sha256: str

    def fixtures(self):
        return FixtureIndex(self.records)

    @property
    def request_evidence(self):
        return tuple(c.evidence for spec in self.specs for c in spec.cells)


def build_control_book(
    items,
    *,
    vectors_payload: bytes,
    authority,
    limits,
    run_id,
    config=None,
    outcome_overrides=None,
    cost_overrides=None,
):
    """Create synthetic captures before Ledger construction; this grants no real data authority."""
    if type(vectors_payload) is not bytes or len(vectors_payload) > 2_000_000:
        raise ValueError("control_vectors_bytes")
    vectors = decode_wire_json(vectors_payload.decode("utf-8"), max_bytes=2_000_000)
    if (
        not isinstance(vectors, dict)
        or set(vectors) != {"evidence_status", "fixtures", "kind", "schema_version"}
        or vectors["kind"] != "authored_request_vectors"
        or type(vectors["schema_version"]) is not int
        or vectors["schema_version"] != 1
        or vectors["evidence_status"] != "synthetic_implementation"
    ):
        raise ValueError("authored_vectors_required")
    rows = vectors["fixtures"]
    if (
        not isinstance(rows, list)
        or not 1 <= len(rows) <= 300
        or any(
            not isinstance(r, dict)
            or set(r) != {"task_id", "request", "authored_text"}
            or type(r["task_id"]) is not str
            or not isinstance(r["request"], dict)
            or type(r["authored_text"]) is not str
            for r in rows
        )
    ):
        raise ValueError("control_vectors_shape")
    by_id = {row["task_id"]: row for row in rows}
    if len(by_id) != len(rows):
        raise ValueError("control_vectors_ids")
    config = config or control_config()
    specs = []
    records = []
    overrides = outcome_overrides or {}
    costs = cost_overrides or {}
    items = tuple(items)
    if not items or len({i.prompt_id for i in items}) != len(items):
        raise ValueError("control_items")
    for item in items:
        row = by_id.get(item.prompt_id)
        if (
            row is None
            or type(row["authored_text"]) is not str
            or len(row["authored_text"].encode()) > 15_000
        ):
            raise ValueError("control_vectors_binding")
        request = row["request"]
        if request.get("messages") != [
            {"role": "user", "content": [{"type": "text", "text": item.text}]}
        ]:
            raise ValueError("control_public_prompt_binding")
        text = "FINAL: " + row["authored_text"]
        cheap_override = overrides.get((item.prompt_id, "cheap"))
        cheap_text = text if cheap_override is None else cheap_override.text
        spec = build_matrix_spec(
            item,
            config=config,
            authority=authority,
            limits=limits,
            run_id=run_id,
            operation_id="matrix-" + canonical_digest([run_id, item.prompt_id])[:32],
            cheap_text=cheap_text,
        )
        specs.append(spec)
        eligible = can_handle(item.text)
        for cell in spec.cells:
            requested = cell.prepared.request
            model = cell.provider_config.models[requested.model].reported_models[0]
            typed = None
            parsed = None
            output = None
            if cell.name in {"cheap", "frontier"}:
                output = text
            elif cell.name == "classifier":
                parsed = {"tier": "code" if eligible else "cheap", "confidence": 0.9}
            elif cell.name == "check_llm":
                parsed = {"accept": True, "probability": 0.9}
            elif cell.name == "gate":
                level = 0 if eligible else 1
                typed = TypedJudgmentResult.model_validate(
                    dict(
                        schema_version=1,
                        model_reported=model,
                        question_spec_sha256=requested.question_spec_sha256,
                        answers={
                            "route": dict(
                                type="score",
                                score=float(level),
                                legend={str(i): v for i, v in enumerate(SCORE_LEVELS)},
                                probabilities={str(i): float(i == level) for i in range(4)},
                                confidence=0.9,
                            )
                        },
                    )
                ).validate_for(requested)
            else:
                typed = TypedJudgmentResult.model_validate(
                    dict(
                        schema_version=1,
                        model_reported=model,
                        question_spec_sha256=requested.question_spec_sha256,
                        answers={"accept": dict(type="noul", noul=0.9)},
                    )
                ).validate_for(requested)
            if parsed is not None:
                import json

                output = json.dumps(parsed, separators=(",", ":"))
            outcome = overrides.get(
                (item.prompt_id, cell.name),
                Outcome(
                    "ok",
                    model,
                    NormalizedUsage(),
                    None,
                    text=output,
                    parsed=parsed,
                    typed=typed,
                ),
            )
            # These are explicitly invented counterfactual costs, not observed billing/prices.
            toy_cost = (
                "0.010"
                if cell.name == "frontier"
                else "0.001"
                if cell.name == "cheap"
                else "0.0001"
            )
            records.append(
                synthetic_capture(
                    cell.prepared,
                    context=cell.context,
                    outcome=outcome,
                    capture_id="authored-" + canonical_digest([item.prompt_id, cell.name])[:32],
                    review_receipt_sha256=canonical_digest(
                        ["authored-control-only", hashlib.sha256(vectors_payload).hexdigest()]
                    ),
                    indicative_source_cost_usd=costs.get((item.prompt_id, cell.name), toy_cost),
                )
            )
    # Validate all unique fixture bindings now; no side effect has occurred.
    FixtureIndex(records)
    return ControlBook(tuple(specs), tuple(records), hashlib.sha256(vectors_payload).hexdigest())
