"""what_bug_this_catches: live funding bypass, false mock provenance and unowned deadlines."""

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from llm_router_eval.shared.callledger.models import canonical_digest
from llm_router_eval.shared.providers import ProviderError
from llm_router_eval.shared.providers.sdk import ProjectCredential, SDKTransport


def test_fresh_worker_import_does_not_require_parent_controller_or_models():
    """what_bug_this_catches: worker startup eagerly loads parent accounting schemas."""
    source = Path(sys.modules[SDKTransport.__module__].__file__).resolve().parents[3]
    script = r"""
import importlib.abc
import json
import socket
import sys

sys.path.insert(0, sys.argv[1])

def deny(*args, **kwargs):
    raise RuntimeError("network_disabled")

socket.socket.connect = deny
socket.socket.connect_ex = deny
socket.create_connection = deny
try:
    socket.create_connection(("example.invalid", 443))
except RuntimeError:
    pass
else:
    raise AssertionError("network_guard")

class NoParentModels(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "pydantic" or fullname.startswith("pydantic.") or fullname in {
            "llm_router_eval.shared.callledger",
            "llm_router_eval.shared.providers.models",
            "llm_router_eval.shared.providers.controller",
            "llm_router_eval.shared.providers.adapters",
            "llm_router_eval.shared.providers.captures",
            "llm_router_eval.shared.providers.repair",
            "llm_router_eval.shared.providers.sdk",
        }:
            raise AssertionError("unnecessary_worker_import:" + fullname)
        return None

sys.meta_path.insert(0, NoParentModels())
from llm_router_eval.shared.providers import sdk_worker
print(json.dumps({"worker_source": sdk_worker.__file__, "network_guard": True}))
"""
    env = {
        k: os.environ[k] for k in ("SystemRoot", "WINDIR", "PATH", "TEMP", "TMP") if k in os.environ
    }
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(source)],
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    observation = json.loads(result.stdout)
    assert (
        Path(observation["worker_source"]).resolve()
        == (source / "llm_router_eval/shared/providers/sdk_worker.py").resolve()
    )
    assert observation["network_guard"] is True


def test_public_provider_exports_keep_canonical_object_identity():
    """what_bug_this_catches: deferred exports must not replace public controller types."""
    import importlib

    from llm_router_eval.shared import providers

    expected = {
        "models": ("models", None),
        "ModelSupport": ("adapters", "ModelSupport"),
        "ProviderConfig": ("adapters", "ProviderConfig"),
        "FixtureIndex": ("captures", "FixtureIndex"),
        "Provider": ("controller", "Provider"),
        "ProviderError": ("controller", "ProviderError"),
        "TransportFailure": ("controller", "TransportFailure"),
        "TransportReply": ("controller", "TransportReply"),
        "ContentValidator": ("repair", "ContentValidator"),
        "GenerationRepairPolicy": ("repair", "GenerationRepairPolicy"),
        "build_generation_repair_request": ("repair", "build_generation_repair_request"),
        "ProjectCredential": ("sdk", "ProjectCredential"),
        "SDKTransport": ("sdk", "SDKTransport"),
    }
    assert set(providers.__all__) == set(expected)
    assert set(expected) <= set(dir(providers))
    for name, (module_name, attribute) in expected.items():
        module = importlib.import_module(f"{providers.__name__}.{module_name}")
        assert getattr(providers, name) is (
            module if attribute is None else getattr(module, attribute)
        )
    with pytest.raises(AttributeError):
        _ = providers.not_a_provider_export


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


@pytest.mark.parametrize("phase", ["prepare", "admit"])
def test_unsupported_native_response_cap_refuses_before_funding(rig, phase):
    """what_bug_this_catches: unsupported SDK caps must not reserve funded work."""
    native_rig(rig)
    rig.limits = rig.limits.model_copy(update={"max_response_bytes": 65537})
    transport = SDKTransport("openai_responses", mock=mock({}))
    provider = rig.provider(transport=transport)
    with rig.open() as ledger:
        before = ledger.totals()
        with pytest.raises(ProviderError, match="unsupported_sdk_response_limit"):
            if phase == "prepare":
                provider.prepare(rig.request, context=rig.context(), limits=rig.limits)
            else:
                provider.admit_operation(
                    rig.plan(),
                    ledger=ledger,
                    budget=rig.authority,
                    context=rig.context(),
                    limits=rig.limits,
                )
        assert ledger.totals() == before
    assert transport.last_execution is None


def test_unsupported_native_response_cap_cannot_create_dispatch_liability(rig, monkeypatch):
    """what_bug_this_catches: a pre-send local refusal was charged as unknown dispatch."""
    from llm_router_eval.shared.providers import sdk

    def forbidden(*args, **kwargs):
        pytest.fail("unsupported configuration must never start a native worker")

    monkeypatch.setattr(sdk, "_owned_worker", forbidden)
    native_rig(rig)
    transport = SDKTransport("openai_responses", mock=mock({}))
    provider = rig.provider(transport=transport)
    prepared = provider.prepare(rig.request, context=rig.context(), limits=rig.limits)
    rig.evidence = [
        rig.evidence[0].model_copy(
            update={"dispatched_envelope_sha256": prepared.dispatched_envelope_sha256}
        )
    ]
    provider = rig.provider(transport=transport)
    rig.limits = rig.limits.model_copy(update={"max_response_bytes": 65537})
    with rig.open() as ledger:
        # Direct Ledger admission is legitimate; Provider must still recheck its
        # narrower transport contract before transferring any reservation.
        allocation = ledger.admit_operation(
            rig.plan(), context=rig.context(), limits=rig.limits, mode="simulation"
        )
        before = ledger.totals()
        with pytest.raises(ProviderError):
            provider.run(rig.request, **rig.kwargs(ledger, allocation))
        assert ledger.totals() == before
        assert ledger.totals()["dispatched_attempts"] == 0
        assert ledger.totals()["unknown_liability_usd"] == "0"
    assert transport.last_execution is None


@pytest.mark.parametrize("response_cap", [1, 65536])
def test_supported_native_response_cap_still_prepares_and_admits(rig, response_cap):
    native_rig(rig)
    rig.limits = rig.limits.model_copy(update={"max_response_bytes": response_cap})
    transport = SDKTransport("openai_responses", mock=mock({}))
    provider = rig.provider(transport=transport)
    provider.prepare(rig.request, context=rig.context(), limits=rig.limits)
    with rig.open() as ledger:
        allocation = provider.admit_operation(
            rig.plan(),
            ledger=ledger,
            budget=rig.authority,
            context=rig.context(),
            limits=rig.limits,
        )
        assert allocation.operation_id == "op1"
        assert ledger.totals()["dispatched_attempts"] == 0
    assert transport.last_execution is None
