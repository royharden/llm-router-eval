"""what_bug_this_catches: unproven repair calls, counter resets and lost first-call liability.

All inputs, prices, token proofs and provider responses are authored implementation controls.
"""

import json
from dataclasses import FrozenInstanceError
from datetime import timedelta
from decimal import Decimal

import pytest

from llm_router_eval.shared.callledger import LedgerError
from llm_router_eval.shared.callledger.models import canonical_digest as digest
from llm_router_eval.shared.providers import (
    GenerationRepairPolicy,
    ProviderError,
    TransportFailure,
    build_generation_repair_request,
)
from llm_router_eval.shared.providers.models import ImagePart


class Validator:
    revision_sha256 = digest("authored-validator-v1")

    def validate(self, parsed_json):
        return parsed_json["tier"] == "review"


def configure(rig, validator=None):
    policy = GenerationRepairPolicy(
        schema_version=1,
        mode="regenerate_original_v1",
        base_config_sha256=rig.context().config_sha256,
        content_validator_revision_sha256=(
            None if validator is None else validator.revision_sha256
        ),
    )
    context = rig.context
    rig.context = lambda *a, **kw: context(*a, **kw).model_copy(
        update={"config_sha256": policy.bound_config_sha256}
    )
    rig.limits = rig.limits.model_copy(update={"max_content_repairs": 1})
    rig.evidence = []
    rig.register(rig.request)
    repair = build_generation_repair_request(rig.request, policy)
    rig.register(repair)
    return policy, repair


def admit_repair(rig, provider, ledger):
    plan = rig.plan()
    plan = plan.model_copy(
        update={"children": [c.model_copy(update={"max_repairs": 1}) for c in plan.children]}
    )
    return provider.admit_operation(
        plan, ledger=ledger, budget=rig.authority, context=rig.context(), limits=rig.limits
    )


def body(rig, text='{"tier":"review"}'):
    value = rig.body()
    if rig.config.flavor == "openai_responses":
        value["output"][0]["content"][0]["text"] = text
    elif rig.config.flavor == "openai_chat":
        value["choices"][0]["message"]["content"] = text
    else:
        value["content"][0]["text"] = text
    return value


def counters(ledger, rig, allocation):
    return ledger.child_counters(allocation, context=rig.context(child="child0"))


@pytest.mark.parametrize("text", ["not JSON CANARY_BAD_RESPONSE", '{"wrong":0}'])
@pytest.mark.parametrize("flavor", ["openai_responses", "openai_chat", "anthropic"])
def test_actual_schema_regeneration_two_physical_requests_and_accounting(
    factory, tmp_path, spy, reply, text, flavor
):
    rig = factory(tmp_path / "repair.sqlite3", flavor)
    policy, repair = configure(rig)
    transport = spy(rig, [reply(body(rig, text)), reply(body(rig))])
    provider = rig.provider(transport=transport)
    with rig.open() as ledger:
        allocation = admit_repair(rig, provider, ledger)
        result = provider.run(
            rig.request, generation_repair=policy, **rig.kwargs(ledger, allocation)
        )
        assert result.status == "ok" and result.parsed == {"tier": "review"}
        assert len(transport.calls) == len(result.attempt_ids) == 2
        assert all(call[1]["max_retries"] == 0 for call in transport.calls)
        assert counters(ledger, rig, allocation).repairs_used == 1
        assert counters(ledger, rig, allocation).used_attempts == 2
        assert result.normalized_usage.input_tokens == 200
        assert Decimal(result.source_capture.cost_usd) == Decimal("0.02")
        assert Decimal(ledger.totals()["known_spend_usd"]) == Decimal("0.02")
        assert Decimal(ledger.totals()["future_holds_usd"]) == 0
        original_sha = rig.prepared().request_identity_sha256
        repair_sha = rig.prepared(repair).request_identity_sha256
        rows = ledger.connection.execute(
            "SELECT request_sha FROM attempts ORDER BY rowid"
        ).fetchall()
        assert [row[0] for row in rows] == [original_sha, repair_sha]
        assert result.source_capture.request_identity_sha256 == repair_sha != original_sha
        assert b"CANARY_BAD_RESPONSE" not in transport.calls[-1][0]
        assert ledger.operation_state(allocation) == "complete"


def test_content_false_regenerates_and_complete_forwards_opt_in(rig, spy, reply):
    validator = Validator()
    policy, _ = configure(rig, validator)
    transport = spy(rig, [reply(rig.body()), reply(body(rig))])
    provider = rig.provider(transport=transport)
    with rig.open() as ledger:
        allocation = admit_repair(rig, provider, ledger)
        result = provider.complete(
            [m.model_dump(mode="json") for m in rig.request.messages],
            schema=rig.request.output_schema,
            model=rig.request.model,
            max_output_tokens=rig.request.max_output_tokens,
            generation_repair=policy,
            content_validator=validator,
            **rig.kwargs(ledger, allocation),
        )
        assert result.status == "ok" and result.parsed["tier"] == "review"
        assert counters(ledger, rig, allocation).repairs_used == 1


@pytest.mark.parametrize("failure", ["exception", "nonbool", "mutate", "deadline"])
def test_validator_failures_stop_and_do_not_mutate_validated_output(rig, spy, reply, failure):
    class Broken(Validator):
        def validate(self, parsed_json):
            if failure == "exception":
                raise RuntimeError("CANARY_SECRET_VALIDATOR_ERROR")
            if failure == "nonbool":
                return 1
            if failure == "deadline":
                rig.tick += 35
                return False
            parsed_json["tier"] = "unsupported mutation"
            return True

    validator = Broken()
    policy, _ = configure(rig, validator)
    transport = spy(rig, [reply(rig.body())])
    provider = rig.provider(transport=transport)
    with rig.open() as ledger:
        allocation = admit_repair(rig, provider, ledger)
        result = provider.run(
            rig.request,
            generation_repair=policy,
            content_validator=validator,
            **rig.kwargs(ledger, allocation),
        )
        assert len(transport.calls) == 1 and counters(ledger, rig, allocation).repairs_used == 0
        if failure == "mutate":
            assert result.parsed == {"tier": "cheap"}
        else:
            assert result.status != "ok" and result.parsed is None
        output = rig.path.with_suffix(".jsonl")
        ledger.export_jsonl(output)
        assert "CANARY_SECRET_VALIDATOR_ERROR" not in output.read_text()


@pytest.mark.parametrize("missing", ["provider", "ledger"])
def test_missing_second_exact_request_evidence_rejects_before_first_send(rig, spy, missing):
    policy, _ = configure(rig)
    complete_evidence = list(rig.evidence)
    transport = spy(rig, [])
    if missing == "provider":
        rig.evidence = complete_evidence[:1]
    provider = rig.provider(transport=transport)
    rig.evidence = complete_evidence if missing == "provider" else complete_evidence[:1]
    with rig.open() as ledger:
        allocation = admit_repair(rig, provider, ledger)
        with pytest.raises(ValueError, match="untrusted_request"):
            provider.run(rig.request, generation_repair=policy, **rig.kwargs(ledger, allocation))
        assert not transport.calls and counters(ledger, rig, allocation).used_attempts == 0


@pytest.mark.parametrize("mutation", ["config", "revision", "caller", "stored", "capture", "mode"])
def test_policy_and_scope_binding_fail_closed_without_effects(rig, spy, mutation):
    validator = Validator()
    policy, _ = configure(rig, validator)
    transport = spy(rig, [])
    provider = rig.provider(transport=transport)
    with rig.open() as ledger:
        allocation = admit_repair(rig, provider, ledger)
        kwargs = rig.kwargs(ledger, allocation)
        if mutation == "config":
            kwargs["context"] = kwargs["context"].model_copy(update={"config_sha256": digest("x")})
        elif mutation == "revision":
            validator.revision_sha256 = digest("changed revision")
        elif mutation == "caller":
            kwargs["limits"] = rig.limits.model_copy(update={"max_content_repairs": 0})
        elif mutation == "stored":
            # Separately admitted default max_repairs0 is not silently widened.
            ledger.close()
            rig.path = rig.path.with_name("default.sqlite3")
            ledger = rig.open()
            allocation = provider.admit_operation(
                rig.plan(),
                ledger=ledger,
                budget=rig.authority,
                context=rig.context(),
                limits=rig.limits,
            )
            kwargs = rig.kwargs(ledger, allocation)
        elif mutation == "capture":
            provider.capture_mode = "fixture_replay"
        else:
            provider.mode = "strict_replay"
        try:
            with pytest.raises(ProviderError):
                provider.run(
                    rig.request, generation_repair=policy, content_validator=validator, **kwargs
                )
            assert not transport.calls and counters(ledger, rig, allocation).used_attempts == 0
        finally:
            if mutation == "stored":
                ledger.close()


@pytest.mark.parametrize("kind", ["schema_less", "stream", "typed"])
def test_unsupported_request_rejects_without_even_requiring_accounting(rig, kind):
    policy = GenerationRepairPolicy(
        schema_version=1, mode="regenerate_original_v1", base_config_sha256=digest("base")
    )
    request = rig.request.model_copy(
        update={
            "output_schema": None if kind == "schema_less" else rig.request.output_schema,
            "stream": kind == "stream",
        }
    )
    if kind == "typed":
        request = object()
    with pytest.raises(ProviderError, match="generation_repair_binding"):
        rig.provider().run(
            request,
            generation_repair=policy,
            purpose="x",
            ledger=None,
            budget=None,
            context=None,
            limits=None,
            allocation=None,
        )


def test_first_success_releases_unused_hold_without_repair(rig, spy, reply):
    policy, _ = configure(rig)
    transport = spy(rig, [reply(rig.body())])
    provider = rig.provider(transport=transport)
    with rig.open() as ledger:
        allocation = admit_repair(rig, provider, ledger)
        result = provider.run(
            rig.request, generation_repair=policy, **rig.kwargs(ledger, allocation)
        )
        assert result.status == "ok" and len(result.attempt_ids) == 1
        assert counters(ledger, rig, allocation).repairs_used == 0
        assert Decimal(ledger.totals()["future_holds_usd"]) == 0


@pytest.mark.parametrize("order", ["transport_first", "repair_first"])
def test_transport_retry_and_repair_share_two_total_attempts(rig, spy, reply, order):
    policy, _ = configure(rig)
    bad = reply(body(rig, "bad JSON"))
    busy = reply({"error": "synthetic"}, status=429)
    transport = spy(rig, [busy, bad] if order == "transport_first" else [bad, busy])
    provider = rig.provider(transport=transport)
    with rig.open() as ledger:
        allocation = admit_repair(rig, provider, ledger)
        result = provider.run(
            rig.request, generation_repair=policy, **rig.kwargs(ledger, allocation)
        )
        assert result.status != "ok" and len(result.attempt_ids) == len(transport.calls) == 2
        assert counters(ledger, rig, allocation).repairs_used == int(order == "repair_first")
        assert counters(ledger, rig, allocation).used_attempts == 2


@pytest.mark.parametrize("reason", ["model", "envelope", "refusal", "auth", "lost", "overcharge"])
def test_non_content_failures_never_regenerate(rig, spy, reply, reason):
    policy, _ = configure(rig)
    response = body(rig, "bad JSON")
    if reason == "model":
        response["model"] = "not-recognized"
    elif reason == "envelope":
        response["output"] = "not a provider envelope"
    elif reason == "refusal":
        response["output"][0]["content"] = [{"type": "refusal", "refusal": "private"}]
    elif reason == "overcharge":
        response["usage"]["input_tokens"] = 9000
    transport = spy(
        rig,
        [
            TransportFailure("unknown_dispatch")
            if reason == "lost"
            else reply(response, status=401 if reason == "auth" else 200)
        ],
    )
    provider = rig.provider(transport=transport)
    with rig.open() as ledger:
        allocation = admit_repair(rig, provider, ledger)
        if reason == "lost":
            with pytest.raises(ProviderError):
                provider.run(
                    rig.request, generation_repair=policy, **rig.kwargs(ledger, allocation)
                )
        else:
            result = provider.run(
                rig.request, generation_repair=policy, **rig.kwargs(ledger, allocation)
            )
            assert result.status != "ok"
        assert len(transport.calls) == 1 and counters(ledger, rig, allocation).repairs_used == 0


def test_unknown_usage_liability_survives_funded_regeneration(rig, spy, reply):
    policy, _ = configure(rig)
    unknown = body(rig, "bad JSON")
    unknown.pop("usage")
    transport = spy(rig, [reply(unknown), reply(body(rig))])
    provider = rig.provider(transport=transport)
    with rig.open() as ledger:
        allocation = admit_repair(rig, provider, ledger)
        result = provider.run(
            rig.request, generation_repair=policy, **rig.kwargs(ledger, allocation)
        )
        assert result.status == "ok" and result.normalized_usage.input_tokens is None
        assert result.source_capture.cost_usd is None
        assert Decimal(ledger.totals()["unknown_liability_usd"]) == Decimal("0.1")
        assert Decimal(ledger.totals()["known_spend_usd"]) == Decimal("0.01")


def test_counter_observation_is_frozen_read_only_and_validates_scope(rig, spy):
    policy, _ = configure(rig)
    provider = rig.provider(transport=spy(rig, []))
    with rig.open() as ledger:
        allocation = admit_repair(rig, provider, ledger)
        before = list(ledger.connection.iterdump())
        view = counters(ledger, rig, allocation)
        assert (view.used_attempts, view.remaining_attempts, view.repairs_used) == (0, 2, 0)
        with pytest.raises(FrozenInstanceError):
            view.used_attempts = 5
        with pytest.raises(LedgerError, match="context_mismatch"):
            ledger.child_counters(
                allocation,
                context=rig.context(child="child0").model_copy(update={"purpose": "undeclared"}),
            )
        with pytest.raises(LedgerError, match="allocation_mismatch"):
            ledger.child_counters(
                allocation.model_copy(update={"plan_sha256": digest("other")}),
                context=rig.context(child="child0"),
            )
        assert before == list(ledger.connection.iterdump())
        assert not ledger.connection.in_transaction
        rig.now += timedelta(hours=2)
        assert counters(ledger, rig, allocation) == view
        with pytest.raises(ValueError, match="deadline"):
            provider.run(rig.request, generation_repair=policy, **rig.kwargs(ledger, allocation))


def test_restart_observes_actual_repair_and_refuses_automatic_continuation(rig, spy, reply):
    policy, _ = configure(rig)
    transport = spy(rig, [reply(body(rig, "bad JSON")), reply(body(rig))])
    provider = rig.provider(transport=transport)
    with rig.open() as ledger:
        allocation = admit_repair(rig, provider, ledger)
        provider.run(rig.request, generation_repair=policy, **rig.kwargs(ledger, allocation))
        expected = counters(ledger, rig, allocation)
    with rig.open() as ledger:
        assert counters(ledger, rig, allocation) == expected
        before = list(ledger.connection.iterdump())
        with pytest.raises(ProviderError, match="generation_repair_fresh_child"):
            provider.run(rig.request, generation_repair=policy, **rig.kwargs(ledger, allocation))
        assert len(transport.calls) == 2 and before == list(ledger.connection.iterdump())
        ledger.connection.execute("UPDATE authorities SET halted=1")
        ledger.connection.commit()
        assert counters(ledger, rig, allocation) == expected


def test_metadata_excludes_raw_payload_and_exception_canaries_with_positive_control(
    rig, spy, reply
):
    policy, _ = configure(rig)
    transport = spy(rig, [reply(body(rig, "CANARY_BAD_RESPONSE")), reply(body(rig))])
    with rig.open() as ledger:
        provider = rig.provider(transport=transport)
        allocation = admit_repair(rig, provider, ledger)
        provider.run(rig.request, generation_repair=policy, **rig.kwargs(ledger, allocation))
        path = rig.path.with_suffix(".jsonl")
        ledger.export_jsonl(path)
        text = path.read_text()
        canaries = (
            "SYNTHETIC_PAYLOAD_CANARY",
            "CANARY_BAD_RESPONSE",
            "CANARY_SECRET_VALIDATOR_ERROR",
        )

        def scan(value):
            return any(canary in value for canary in canaries)

        assert not scan(text)
        assert scan(text + json.dumps({"bad": canaries[0]}))


def test_actual_native_typed_and_image_requests_rejected_before_effects(factory, tmp_path):
    rig = factory(tmp_path / "typed.sqlite3", "typesafe", True)
    policy = GenerationRepairPolicy(
        schema_version=1, mode="regenerate_original_v1", base_config_sha256=digest("base")
    )
    requests = [rig.request]
    generation = factory(tmp_path / "image.sqlite3")
    image = ImagePart(
        type="local_image",
        execution_ref="authored.png",
        content_sha256=digest("image"),
        media_type="image/png",
        byte_length=12,
        decoded_width=1,
        decoded_height=1,
        rendition_sha256=digest("rendition"),
        source_generation="authored",
        transform_sha256=digest("transform"),
        detail="low",
    )
    requests.append(
        generation.request.model_copy(
            update={
                "messages": [generation.request.messages[0].model_copy(update={"content": [image]})]
            }
        )
    )
    for request in requests:
        with pytest.raises(ProviderError, match="generation_repair_binding"):
            generation.provider().run(
                request,
                generation_repair=policy,
                purpose="x",
                ledger=None,
                budget=None,
                context=None,
                limits=None,
                allocation=None,
            )


def test_second_request_over_funded_input_bound_refuses_before_dispatch(rig, spy):
    policy, _ = configure(rig)
    rig.evidence[-1] = rig.evidence[-1].model_copy(update={"input_token_count": 1001})
    transport = spy(rig, [])
    provider = rig.provider(transport=transport)
    with rig.open() as ledger:
        allocation = admit_repair(rig, provider, ledger)
        with pytest.raises(LedgerError, match="request_bound"):
            provider.run(rig.request, generation_repair=policy, **rig.kwargs(ledger, allocation))
        assert not transport.calls and counters(ledger, rig, allocation).used_attempts == 0


def test_interrupted_first_attempt_preserves_active_liability_and_refuses_resume(rig, spy):
    policy, _ = configure(rig)
    transport = spy(rig, [])
    provider = rig.provider(transport=transport)
    with rig.open() as ledger:
        allocation = admit_repair(rig, provider, ledger)
        ledger.begin_attempt(
            allocation,
            context=rig.context(child="child0"),
            request=rig.evidence[0],
            caller_limits=rig.limits,
        )
    with rig.open() as ledger:
        before = list(ledger.connection.iterdump())
        with pytest.raises(ProviderError, match="generation_repair_fresh_child"):
            provider.run(rig.request, generation_repair=policy, **rig.kwargs(ledger, allocation))
        assert before == list(ledger.connection.iterdump()) and not transport.calls
        assert Decimal(ledger.totals()["active_holds_usd"]) == Decimal("0.1")
        assert counters(ledger, rig, allocation).used_attempts == 1
        assert counters(ledger, rig, allocation).repairs_used == 0


def test_lost_final_repair_body_never_relabels_first_body_as_final(rig, spy, reply):
    policy, _ = configure(rig)
    transport = spy(rig, [reply(body(rig, "bad JSON")), TransportFailure("unknown_dispatch")])
    provider = rig.provider(transport=transport)
    with rig.open() as ledger:
        allocation = admit_repair(rig, provider, ledger)
        with pytest.raises(ProviderError, match="unknown_dispatch"):
            provider.run(rig.request, generation_repair=policy, **rig.kwargs(ledger, allocation))
        assert len(transport.calls) == 2 and counters(ledger, rig, allocation).repairs_used == 1
        assert Decimal(ledger.totals()["unknown_liability_usd"]) == Decimal("0.1")


def test_counter_scope_uses_declared_child_purpose_with_distinct_parent_purpose(rig, spy):
    policy, _ = configure(rig)
    context = rig.context
    rig.context = lambda *a, **kw: (
        context(*a, **kw).model_copy(update={"purpose": "operation.group"})
        if context(*a, **kw).child_id == "group"
        else context(*a, **kw)
    )
    provider = rig.provider(transport=spy(rig, []))
    with rig.open() as ledger:
        allocation = admit_repair(rig, provider, ledger)
        assert counters(ledger, rig, allocation).used_attempts == 0
        with pytest.raises(LedgerError, match="context_mismatch"):
            ledger.child_counters(
                allocation,
                context=rig.context(child="child0").model_copy(
                    update={"purpose": "operation.group"}
                ),
            )
