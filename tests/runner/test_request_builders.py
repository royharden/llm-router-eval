"""Frozen exact-source vectors from pre-refactor policy execution, not model captures."""

import json
from pathlib import Path

import pytest

from llm_router_eval.shared.callledger.models import CallContext, Limits
from llm_router_eval.shared.providers.adapters import ProviderConfig, prepare_request
from llm_router_eval.shared.routing import RouteContext, RouteInput
from llm_router_eval.shared.routing.policies import (
    checker_request,
    classifier_request,
    gate_request,
)

VECTORS = json.loads(
    (Path(__file__).with_name("request-builder-vectors.json")).read_text(encoding="utf-8")
)


@pytest.mark.parametrize("vector", VECTORS, ids=[v["policy"] for v in VECTORS])
def test_extracted_builder_keeps_exact_old_wire_identity_and_payload(vector, request):
    """what_bug_this_catches: extraction changes unset criteria, identity or payload bytes."""
    request.node.user_properties.append(
        (
            "what_bug_this_catches",
            test_extracted_builder_keeps_exact_old_wire_identity_and_payload.__doc__,
        )
    )

    def unused(*args, **kwargs):
        raise AssertionError("Pure builder must not execute inference")

    item = RouteInput(prompt_id="unit-task", text="What is a mammal?")
    context = RouteContext(
        item=item,
        cheap_model="fixture-luna",
        jev_model="fixture-jev",
        answer=unused,
        generate=unused,
        judge=unused,
        config_sha256=vector["context"]["config_sha256"],
    )
    if vector["policy"] == "classifier":
        built = classifier_request(item, context)
    elif vector["policy"] == "jev_gate":
        built = gate_request(item, context)
    else:
        built = checker_request(
            item,
            "Reasoning.\nFINAL: A",
            context,
            backend="jev" if vector["policy"] == "cascade_jev" else "llm",
        )
    assert built.model_dump(mode="json", by_alias=True) == vector["request"]
    assert built.identity_payload() == vector["identity_payload"]
    prepared = prepare_request(
        built,
        config=ProviderConfig.model_validate(vector["provider_config"]),
        context=CallContext.model_validate(vector["context"]),
        limits=Limits.model_validate(vector["limits"]),
    )
    assert prepared.payload_json.decode() == vector["payload_json"]
    assert prepared.request_identity_sha256 == vector["request_identity_sha256"]
    assert prepared.dispatched_envelope_sha256 == vector["dispatched_envelope_sha256"]
