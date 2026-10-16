"""what_bug_this_catches: live funding bypass, false mock provenance and unowned deadlines."""

import base64
import json
import sys

import pytest

from llm_router_eval.shared.callledger.models import canonical_digest
from llm_router_eval.shared.providers import ProviderError
from llm_router_eval.shared.providers.sdk import ProjectCredential, SDKTransport


def payload():
    return json.dumps(
        dict(
            model="gpt-6-luna",
            input=[dict(role="user", content=[dict(type="input_text", text="Authored toy")])],
            max_output_tokens=512,
            stream=False,
            store=False,
            tools=[],
            tool_choice="none",
        )
    ).encode()


def mock(body, **extra):
    return dict(
        status_code=200, body_base64=base64.b64encode(json.dumps(body).encode()).decode(), **extra
    )


def test_prepare_binds_sdk_order_before_evidence_and_does_not_import_sdk():
    before = set(sys.modules)
    t = SDKTransport("openai_responses", mock=mock({}))
    prepared = t.prepare_payload(payload())
    assert list(json.loads(prepared)) == [
        "model",
        "stream",
        "service_tier",
        "input",
        "max_output_tokens",
        "store",
        "tool_choice",
        "tools",
    ]
    assert prepared != payload() and t.prepare_payload(prepared) == prepared
    assert not any(
        n.split(".")[0] in {"openai", "typesafe_sdk", "httpx2"} for n in set(sys.modules) - before
    )


def test_explicit_mock_cannot_enter_live_and_generic_spy_cannot_claim_live(rig, spy):
    transport = SDKTransport("openai_responses", mock=mock({}))
    with pytest.raises(ProviderError, match="unsupported_live_transport"):
        rig.provider(mode="live", transport=transport)
    with pytest.raises(ProviderError, match="unsupported_live_transport"):
        rig.provider(mode="live", transport=spy(rig, []))
    with pytest.raises(ValueError, match="explicit_sdk_mode"):
        SDKTransport("openai_responses")


def test_live_uses_external_ledger_and_refuses_synthetic_funding(rig):
    t = SDKTransport(
        "openai_responses",
        credential=ProjectCredential("test-account-handle", "dummy-offline-not-a-real-key"),
    )
    p = rig.provider(mode="live", transport=t)
    assert p.ledger_mode == "external"
    limits = rig.limits.model_copy(update={"max_attempts_total": 1})
    with rig.open() as ledger:
        with pytest.raises(ValueError, match="synthetic_not_live_proof"):
            p.admit_operation(
                rig.plan(attempts=1),
                ledger=ledger,
                budget=rig.authority,
                context=rig.context(),
                limits=limits,
            )
        assert ledger.totals()["dispatched_attempts"] == 0
        with pytest.raises(ProviderError, match="unsupported_live_retries"):
            p.admit_operation(
                rig.plan(),
                ledger=ledger,
                budget=rig.authority,
                context=rig.context(),
                limits=rig.limits,
            )
    assert t.last_execution is None


@pytest.mark.parametrize(
    "mutation",
    [
        {"model": "other"},
        {"stream": True},
        {"tools": [{"type": "web_search"}]},
        {"max_output_tokens": 513},
        {"service_tier": "priority"},
        {"store": True},
        {"input": [{"role": "user", "content": [{"type": "input_image", "image_url": "secret"}]}]},
    ],
)
def test_native_scope_rejects_before_worker(mutation):
    t = SDKTransport("openai_responses", mock=mock({}))
    body = json.loads(payload())
    body.update(mutation)
    with pytest.raises(ValueError):
        t.prepare_payload(json.dumps(body).encode())
    assert t.last_execution is None


def test_credential_repr_never_contains_key():
    key = "EXPLICIT_DUMMY_KEY_CANARY"
    credential = ProjectCredential("authored-account", key)
    assert key not in repr(credential)


def native_rig(rig, model="gpt-6-luna"):
    rig.config = rig.config.model_copy(
        update={
            "models": {
                model: next(iter(rig.config.models.values())).model_copy(
                    update={"reported_models": [model]}
                )
            }
        }
    )
    rig.request = rig.request.model_copy(update={"model": model})
    rig.policy = rig.policy.model_copy(update={"model": model})
    rig.snapshot = rig.snapshot.model_copy(
        update={
            "policies": [rig.policy],
            "data_sha256": canonical_digest([rig.policy.model_dump(mode="json")]),
        }
    )
    rig.bound = rig.bound.model_copy(
        update={
            "snapshot_sha256": rig.snapshot.snapshot_sha256,
            "policy_sha256": rig.policy.policy_sha256,
            "provider_config_sha256": rig.config.sha256,
            "model": model,
        }
    )
    rig.evidence = []
    rig.register(rig.request)
    original_plan = rig.plan

    def plan(**kwargs):
        value = original_plan(**kwargs)
        return value.model_copy(
            update={
                "children": [child.model_copy(update={"model": model}) for child in value.children]
            }
        )

    rig.plan = plan
    return rig


@pytest.mark.parametrize(
    "flavor,typed,model",
    [
        ("openai_responses", False, "gpt-6-luna"),
        ("typesafe", True, "jev-1.13.0"),
    ],
)
def test_actual_native_sdk_mock_joins_real_ledger_without_live_claim(
    factory, tmp_path, flavor, typed, model
):
    pytest.importorskip("openai", reason="install the optional sdk extra")
    rig = factory(tmp_path / "native.sqlite3", flavor, typed)
    native_rig(rig, model)
    body = rig.body()
    body["model"] = model
    t = SDKTransport(flavor, mock=mock(body))
    p = rig.provider(transport=t)
    prepared = p.prepare(rig.request, context=rig.context(), limits=rig.limits)
    if not typed:
        assert prepared.dispatched_envelope_sha256 != rig.evidence[0].dispatched_envelope_sha256
    rig.evidence = [
        rig.evidence[0].model_copy(
            update={"dispatched_envelope_sha256": prepared.dispatched_envelope_sha256}
        )
    ]
    p = rig.provider(transport=t)
    with rig.open() as ledger:
        allocation = p.admit_operation(
            rig.plan(),
            ledger=ledger,
            budget=rig.authority,
            context=rig.context(),
            limits=rig.limits,
        )
        result = p.run(rig.request, **rig.kwargs(ledger, allocation))
        assert result.status == "ok" and result.origin == "mock"
        assert (
            result.source_capture.origin == "synthetic" and result.current_external_cost_usd == "0"
        )
        assert ledger.totals()["dispatched_attempts"] == 1
        assert (
            t.last_execution["owned_tree_closed"]
            and t.last_execution["active_owned_processes_after"] == 0
        )


def test_owned_worker_closes_blocked_native_mock_and_keeps_unknown_liability(rig):
    pytest.importorskip("openai", reason="install the optional sdk extra")
    native_rig(rig)
    # Startup is inside the wall limit; the progress event proves the SDK reached
    # its mock physical send before cancellation of the intentionally blocked I/O.
    rig.limits = rig.limits.model_copy(update={"attempt_timeout_s": 10.0})
    t = SDKTransport("openai_responses", mock=mock({}, delay_s=30))
    p = rig.provider(transport=t)
    prepared = p.prepare(rig.request, context=rig.context(), limits=rig.limits)
    rig.evidence = [
        rig.evidence[0].model_copy(
            update={"dispatched_envelope_sha256": prepared.dispatched_envelope_sha256}
        )
    ]
    p = rig.provider(transport=t)
    with rig.open() as ledger:
        allocation = p.admit_operation(
            rig.plan(),
            ledger=ledger,
            budget=rig.authority,
            context=rig.context(),
            limits=rig.limits,
        )
        with pytest.raises(ProviderError, match="deadline"):
            p.run(rig.request, **rig.kwargs(ledger, allocation))
        assert ledger.totals()["dispatched_attempts"] == 1
        assert t.last_execution["timed_out"] and t.last_execution["physical_dispatch_observed"]
        assert (
            t.last_execution["owned_tree_closed"]
            and t.last_execution["active_owned_processes_after"] == 0
        )
        assert t.last_execution["elapsed_seconds"] < 16
        assert ledger.totals()["known_spend_usd"] == "0"
        assert ledger.totals()["unknown_liability_usd"] != "0"


def test_transport_rejects_retry_without_worker():
    t = SDKTransport("openai_responses", mock=mock({}))
    with pytest.raises(ValueError):
        t.send(t.prepare_payload(payload()), timeout_s=1, max_retries=1, max_response_bytes=65536)
    assert t.last_execution is None
