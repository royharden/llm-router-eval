"""what_bug_this_catches: semantic success cannot complete unknown billing or erase a dispatch."""

from decimal import Decimal

import pytest
from test_sdk_transport import mock, native_rig

from llm_router_eval.shared.providers import SDKTransport


@pytest.mark.parametrize(
    "flavor,typed,model",
    [("openai_responses", False, "gpt-6-luna"), ("typesafe", True, "jev-1.13.0")],
)
@pytest.mark.parametrize("usage_case", ["absent", "null", "malformed", "partial", "known"])
def test_native_answer_requires_known_settlement_for_completion(
    factory, tmp_path, flavor, typed, model, usage_case
):
    pytest.importorskip("openai", reason="install the optional sdk extra")
    rig = native_rig(factory(tmp_path / "ledger.sqlite3", flavor, typed), model)
    body = rig.body()
    body["model"] = model
    if usage_case == "absent":
        body.pop("usage")
    elif usage_case == "null":
        body["usage"] = None
    elif usage_case == "malformed":
        body["usage"] = {"input_tokens": "unreported", "output_tokens": 0}
    elif usage_case == "partial":
        body["usage"] = {"output_tokens": 0}
    _joined(rig, body, known=usage_case == "known")


@pytest.mark.parametrize("response_case", ["refused", "retryable_error"])
def test_unknown_usage_keeps_refusal_and_stops_hidden_retry(rig, response_case):
    pytest.importorskip("openai", reason="install the optional sdk extra")
    native_rig(rig)
    body = rig.body()
    body["model"] = "gpt-6-luna"
    body.pop("usage")
    status = 200
    expected = "refused"
    if response_case == "refused":
        body["output"] = [
            {"type": "message", "content": [{"type": "refusal", "refusal": "Authored refusal"}]}
        ]
    else:
        body = {"error": {"message": "Authored retryable error"}}
        status = 429
        expected = "provider_error"
    _joined(rig, body, status=status, expected=expected)


def _joined(rig, body, *, known=False, status=200, expected="unknown"):
    control = mock(body)
    control["status_code"] = status
    transport = SDKTransport(rig.config.flavor, mock=control)
    provider = rig.provider(transport=transport)
    prepared = provider.prepare(rig.request, context=rig.context(), limits=rig.limits)
    rig.evidence = [
        rig.evidence[0].model_copy(
            update={"dispatched_envelope_sha256": prepared.dispatched_envelope_sha256}
        )
    ]
    provider = rig.provider(transport=transport)
    with rig.open() as ledger:
        allocation = provider.admit_operation(
            rig.plan(attempts=2),
            ledger=ledger,
            budget=rig.authority,
            context=rig.context(),
            limits=rig.limits,
        )
        result = provider.run(rig.request, **rig.kwargs(ledger, allocation))
        totals = ledger.totals()
        assert len(result.attempt_ids) == totals["dispatched_attempts"] == 1
        assert result.accounting_ref == allocation.allocation_id
        assert result.origin == "mock" and result.source_capture.origin == "synthetic"
        assert result.current_external_cost_usd == "0"
        assert transport.last_execution["simulation"]
        assert transport.last_execution["physical_dispatch_observed"]
        assert transport.last_execution["owned_tree_closed"]
        assert transport.last_execution["active_owned_processes_after"] == 0
        assert Decimal(totals["active_holds_usd"]) == Decimal(totals["future_holds_usd"]) == 0
        if known:
            assert result.status == "ok" and ledger.operation_state(allocation) == "complete"
            assert Decimal(totals["unknown_liability_usd"]) == 0
            assert Decimal(totals["known_spend_usd"]) == Decimal("0.01")
            assert result.source_capture.cost_usd == "0.01"
        else:
            assert result.status == expected and ledger.operation_state(allocation) == "partial"
            assert result.text is result.parsed is result.typed is None
            assert Decimal(totals["known_spend_usd"]) == 0
            assert Decimal(totals["unknown_liability_usd"]) == Decimal("0.1")
            assert result.source_capture.cost_usd is None
            # Declared caller projection, not execution of D14's separate adapter.
            wire = result.model_dump(mode="json")
            unavailable = {
                "status": "ok" if wire["status"] == "ok" else "unavailable",
                "origin": wire["origin"],
                "accounting_ref": wire["accounting_ref"],
                "attempt_ids": wire["attempt_ids"],
                "source_cost_usd": wire["source_capture"]["cost_usd"],
            }
            assert unavailable["status"] == "unavailable"
            assert unavailable["origin"] != "not_run" and unavailable["accounting_ref"]
            assert len(unavailable["attempt_ids"]) == 1 and unavailable["source_cost_usd"] is None
