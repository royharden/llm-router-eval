"""Actual local Provider/Ledger path on authored fixtures; no benchmark/model/price proof."""

import json
import socket
from pathlib import Path

import pytest

from llm_router_eval.data.controls import make_controls
from llm_router_eval.data.corpus import Task, digest
from llm_router_eval.evaluation.control_book import build_control_book
from llm_router_eval.evaluation.matrix import CELL_PURPOSES, collect_matrix
from llm_router_eval.shared.callledger import Ledger
from llm_router_eval.shared.callledger.models import BudgetAuthority, Limits, NormalizedUsage
from llm_router_eval.shared.providers import FixtureIndex, Provider
from llm_router_eval.shared.providers.adapters import Outcome
from llm_router_eval.shared.providers.captures import synthetic_capture
from llm_router_eval.shared.routing.context import RouteContext, RouteInput
from llm_router_eval.shared.routing.policies import (
    CheckerCalibration,
    Policy,
    calibration_scope,
)
from llm_router_eval.shared.routing.scoring.deterministic import (
    CandidateAnswer,
    ScoringLabel,
    score,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def network_denied(monkeypatch):
    def denied(*a, **kw):
        raise RuntimeError("matrix network forbidden")

    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)


def inputs():
    authority = BudgetAuthority(
        schema_version=1,
        authority_id="authored-offline-authority",
        scope_sha256=digest("UNIT offline only"),
        cap_usd="0",
        max_outbound_attempts=0,
        run_deadline_utc="2090-01-01T00:00:00Z",
    )
    limits = Limits(
        schema_version=1,
        max_attempts_total=2,
        max_content_repairs=0,
        operation_deadline_s=60.0,
        attempt_timeout_s=20.0,
        run_deadline_utc=authority.run_deadline_utc,
        max_outbound_attempts=0,
        max_concurrent_operations=2,
        max_total_input_tokens=100_000,
        max_state_plus_longest_question_tokens=100_000,
        max_request_bytes=128_000,
        max_response_bytes=128_000,
        max_questions=32,
        max_choice_options=64,
        max_total_output_tokens=512,
    )
    return authority, limits


def book_for(tasks=None, **kw):
    authority, limits = inputs()
    tasks = tasks or [make_controls().tasks[0]]
    return build_control_book(
        [RouteInput.from_data_view(t.routing_view()) for t in tasks],
        vectors_payload=(ROOT / "fixtures/implementation-controls/requests.json").read_bytes(),
        authority=authority,
        limits=limits,
        run_id="UNIT-matrix",
        **kw,
    )


def open_ledger(tmp_path, book):
    authority, _ = inputs()
    return Ledger(
        tmp_path / "matrix.sqlite3", authority=authority, trusted_requests=book.request_evidence
    )


def collect(book, ledger, fixtures=None):
    authority, _ = inputs()
    return collect_matrix(
        book.specs[0],
        ledger=ledger,
        budget=authority,
        fixtures=book.fixtures() if fixtures is None else fixtures,
    )


def test_six_children_really_admitted_and_looked_up_without_any_dispatch(tmp_path, monkeypatch):
    book = book_for()
    original = Provider.run
    before_lookup = []

    def checked(self, *args, **kwargs):
        active = kwargs["ledger"]
        before_lookup.append(
            active.connection.execute("SELECT COUNT(*) FROM children").fetchone()[0]
        )
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Provider, "run", checked)
    with open_ledger(tmp_path, book) as ledger:
        matrix = collect(book, ledger)
        children = ledger.connection.execute("SELECT definition,state FROM children").fetchall()
        assert len(children) == 6
        assert {json.loads(r[0])["purpose"] for r in children} == set(CELL_PURPOSES.values())
        assert all(r[1] == "complete" and json.loads(r[0])["max_attempts"] == 0 for r in children)
        assert (
            ledger.connection.execute(
                "SELECT COUNT(*) FROM events WHERE json_extract(payload,'$.kind')='reuse_accounted'"
            ).fetchone()[0]
            == 6
        )
        assert (
            ledger.totals()["dispatched_attempts"] == 0
            and ledger.totals()["total_exposure_usd"] == "0"
        )
        assert ledger.operation_state(matrix.allocation) == "complete"
        assert before_lookup == [6] * 6
        assert all(
            v["origin"] == "mock" and v["current_external_cost_usd"] == "0"
            for v in matrix.safe_projection()
        )
        assert all(
            v["source_latency_ms"] is None and v["lookup_latency_ms"] >= 0
            for v in matrix.safe_projection()
        )
        assert all(
            matrix.cell(n).result.normalized_usage.input_tokens is None for n in CELL_PURPOSES
        )
        assert book.specs[0].root_context.purpose == "matrix.collect"
        with pytest.raises(RuntimeError, match="network forbidden"):
            socket.create_connection(("example.invalid", 443))


@pytest.mark.parametrize(
    "name",
    [
        "always_cheap",
        "always_frontier",
        "rules",
        "classifier",
        "jev_gate",
        "cascade_jev",
        "cascade_llm",
    ],
)
def test_all_seven_policies_consume_real_unique_matrix_spans_in_fresh_contexts(tmp_path, name):
    book = book_for()
    with open_ledger(tmp_path, book) as ledger:
        matrix = collect(book, ledger)
        execution = matrix.execute(Policy(name), ledger=ledger)
        assert execution.answer.text == "FINAL: 5"
        assert len(set(execution.consumed_span_ids)) == len(execution.consumed_span_ids)
        assert execution.answer.span_ids[-1] in execution.consumed_span_ids
        if name.startswith("cascade"):
            assert execution.tier == "frontier" and execution.reasons == ("checker_unvalidated",)
            assert set(execution.consumed_span_ids) == {
                matrix.cell("cheap").span_id,
                matrix.cell("frontier").span_id,
            }
        again = matrix.execute(Policy(name), ledger=ledger)
        assert again.consumed_span_ids == execution.consumed_span_ids
        assert ledger.totals()["dispatched_attempts"] == 0


def test_code_tier_is_actual_solver_and_explicit_non_model_span(tmp_path):
    task = next(t for t in make_controls().tasks if t.kind == "code")
    book = book_for([task])
    with open_ledger(tmp_path, book) as ledger:
        matrix = collect(book, ledger)
        execution = matrix.execute(Policy("rules"), ledger=ledger)
        assert execution.tier == "code" and execution.answer.text == "FINAL: 6"
        assert len(execution.consumed_span_ids) == 1
        events = [
            json.loads(row[0])
            for row in ledger.connection.execute(
                "SELECT payload FROM events WHERE json_extract(payload,'$.kind')='non_model'"
            )
        ]
        code = [r for r in events if r["span"]["context"]["purpose"] == "evaluation.code"]
        assert len(code) == 1 and code[0]["span"]["kind"] == "non_model"
        assert (
            code[0]["span"]["input_tokens"] is None and code[0]["span"]["resource_cost_usd"] is None
        )


def test_private_label_mutation_leaves_prepared_bytes_keys_and_captures_unchanged(tmp_path):
    original = make_controls().tasks[0]
    data = original.model_dump(mode="json")
    data["scoring"]["answer"] = "999"
    data["analysis_difficulty"] = "hard"
    data["row_sha256"] = digest({k: v for k, v in data.items() if k != "row_sha256"})
    changed = Task.model_validate(data)
    before, after = book_for([original]), book_for([changed])
    for left, right in zip(before.specs[0].cells, after.specs[0].cells, strict=True):
        assert left.prepared.payload_json == right.prepared.payload_json
        assert left.evidence.request_identity_sha256 == right.evidence.request_identity_sha256
    assert [r.source.capture_sha256 for r in before.records] == [
        r.source.capture_sha256 for r in after.records
    ]
    assert (
        before.specs[0].root_context.source_generation
        == after.specs[0].root_context.source_generation
    )
    with open_ledger(tmp_path, before) as ledger:
        matrix = collect(before, ledger)
        answer = matrix.execute(Policy("always_cheap"), ledger=ledger).answer
        candidate = CandidateAnswer(prompt_id=original.task_id, status="ok", text=answer.text)
        labels = [
            ScoringLabel(
                prompt_id=t.task_id,
                family_id=t.family_id,
                kind=t.kind,
                answer=t.scoring.answer,
                rubric=t.scoring.rubric,
            )
            for t in [original, changed]
        ]
        assert (
            score(labels[0], candidate).passed is True
            and score(labels[1], candidate).passed is False
        )


def test_strict_cheap_miss_never_falls_back_and_checker_cells_are_not_run(tmp_path):
    book = book_for()
    remaining = [
        r for r, c in zip(book.records, book.specs[0].cells, strict=True) if c.name != "cheap"
    ]
    with open_ledger(tmp_path, book) as ledger:
        matrix = collect(book, ledger, FixtureIndex(remaining))
        assert matrix.cell("cheap").result.status == "cache_miss"
        assert matrix.cell("cheap").result.source_capture is None
        assert matrix.cell("check_jev").result is None and matrix.cell("check_llm").result is None
        assert matrix.cell("check_jev").reason == "cheap_unavailable"
        assert ledger.operation_state(matrix.allocation) == "partial"
        assert ledger.totals()["dispatched_attempts"] == 0
        row = matrix.execute(Policy("cascade_jev"), ledger=ledger)
        assert row.reasons == ("cheap_format_failed",) and row.tier == "frontier"


def test_classifier_invalid_and_unknown_source_cost_are_honest(tmp_path):
    task = make_controls().tasks[0]
    book = book_for(
        outcome_overrides={
            (task.task_id, "classifier"): Outcome(
                "invalid_output", "fixture-cheap-reported", NormalizedUsage(), None
            )
        },
        cost_overrides={(task.task_id, "cheap"): None},
    )
    with open_ledger(tmp_path, book) as ledger:
        matrix = collect(book, ledger)
        assert matrix.cell("cheap").result.source_capture.cost_usd is None
        assert matrix.cell("cheap").result.current_external_cost_usd == "0"
        execution = matrix.execute(Policy("classifier"), ledger=ledger)
        assert execution.reasons == ("classifier_invalid",) and execution.tier == "frontier"


@pytest.mark.parametrize("backend", ["jev", "llm"])
def test_unit_calibration_double_uses_exact_checker_request_and_consumes_once(tmp_path, backend):
    """The synthetic trusted-receipt double exercises callback matching, not real calibration."""
    book = book_for()
    spec = book.specs[0]
    context = RouteContext(
        item=spec.item,
        cheap_model=spec.config.cheap_model,
        jev_model=spec.config.jev_model,
        answer=lambda _: None,
        generate=lambda *_: None,
        judge=lambda *_: None,
        config_sha256=spec.config.sha256,
    )
    receipt = digest("UNIT checker admission double")
    calibration = CheckerCalibration(
        status="validated",
        backend=backend,
        threshold=0.7,
        config_sha256=calibration_scope(context, backend),
        receipt_sha256=receipt,
        dev_responses=40,
        human_fail_cases=20,
        accepted=20,
        false_accepts=0,
    )
    with open_ledger(tmp_path, book) as ledger:
        matrix = collect(book, ledger)
        result = matrix.execute(
            Policy("cascade_" + backend, calibration=calibration),
            ledger=ledger,
            trusted_calibration_receipts=frozenset({receipt}),
        )
        assert result.tier == "cheap" and result.reasons == ("checker_accepted",)
        assert set(result.consumed_span_ids) == {
            matrix.cell("cheap").span_id,
            matrix.cell("check_" + backend).span_id,
        }


def test_mismatched_cheap_capture_refuses_checker_and_closes_only_own_pending_children(tmp_path):
    book = book_for()
    spec = book.specs[0]
    cell = spec.cells[0]
    changed = synthetic_capture(
        cell.prepared,
        context=cell.context,
        outcome=Outcome(
            "ok",
            "fixture-cheap-reported",
            NormalizedUsage(),
            None,
            text="FINAL: CANARY_WRONG_CHEAP",
        ),
        capture_id="authored-mismatch",
        review_receipt_sha256=digest("UNIT mismatch"),
    )
    with open_ledger(tmp_path, book) as ledger:
        with pytest.raises(ValueError, match="^matrix_cheap_response_binding$"):
            collect(book, ledger, FixtureIndex([changed, *book.records[1:]]))
        assert (
            ledger.connection.execute(
                "SELECT COUNT(*) FROM children WHERE state='pending'"
            ).fetchone()[0]
            == 0
        )
        assert (
            ledger.connection.execute(
                "SELECT COUNT(*) FROM events WHERE json_extract(payload,'$.kind')='reuse_accounted'"
            ).fetchone()[0]
            == 1
        )
        assert ledger.totals()["dispatched_attempts"] == 0


def test_safe_projection_excludes_transient_prompt_response_and_metadata_leaks(tmp_path):
    book = book_for()
    with open_ledger(tmp_path, book) as ledger:
        matrix = collect(book, ledger)
        projection = json.dumps(matrix.safe_projection())
        canaries = [book.specs[0].item.text, "FINAL: 5", "probabilities", "usage_raw"]
        assert not any(c in projection for c in canaries)
        assert any(c in projection + canaries[0] for c in canaries)
        cell = matrix.cell("classifier")
        cell.result.parsed["tier"] = "frontier"
        assert matrix.cell("classifier").result.parsed["tier"] == "cheap"


def test_new_actual_code_execution_gets_distinct_span_identity(tmp_path):
    task = next(t for t in make_controls().tasks if t.kind == "code")
    book = book_for([task])
    with open_ledger(tmp_path, book) as ledger:
        matrix = collect(book, ledger)
        first = matrix.execute(Policy("rules"), ledger=ledger)
        second = matrix.execute(Policy("rules"), ledger=ledger)
        assert first.answer.text == second.answer.text
        assert first.consumed_span_ids != second.consumed_span_ids


def test_all_ten_authored_controls_complete_60_real_reuses_and_70_policy_paths(tmp_path):
    book = book_for(make_controls().tasks)
    authority, _ = inputs()
    with open_ledger(tmp_path, book) as ledger:
        executions = []
        for spec in book.specs:
            matrix = collect_matrix(spec, ledger=ledger, budget=authority, fixtures=book.fixtures())
            for name in [
                "always_cheap",
                "always_frontier",
                "rules",
                "classifier",
                "jev_gate",
                "cascade_jev",
                "cascade_llm",
            ]:
                executions.append(matrix.execute(Policy(name), ledger=ledger))
        assert len(executions) == 70 and all(e.answer.status == "ok" for e in executions)
        events = [json.loads(r[0]) for r in ledger.connection.execute("SELECT payload FROM events")]
        assert sum(e["kind"] == "reuse_accounted" for e in events) == 60
        assert ledger.totals()["dispatched_attempts"] == 0


def test_exact_matrix_callback_request_mismatch_cannot_use_another_cell(tmp_path, monkeypatch):
    from llm_router_eval.shared.routing import policies

    book = book_for()
    with open_ledger(tmp_path, book) as ledger:
        matrix = collect(book, ledger)
        original = policies.classifier_request

        def changed(item, context):
            return original(item, context).model_copy(
                update={"model": book.specs[0].config.frontier_model}
            )

        monkeypatch.setattr(policies, "classifier_request", changed)
        with pytest.raises(ValueError, match="matrix_callback_binding"):
            matrix.execute(Policy("classifier"), ledger=ledger)
        assert ledger.totals()["dispatched_attempts"] == 0


def test_nonzero_outbound_authority_is_refused_before_any_operation(tmp_path):
    book = book_for()
    authority, _ = inputs()
    with open_ledger(tmp_path, book) as ledger:
        with pytest.raises(ValueError, match="matrix_zero_quota"):
            collect_matrix(
                book.specs[0],
                ledger=ledger,
                budget=authority.model_copy(update={"max_outbound_attempts": 1}),
                fixtures=book.fixtures(),
            )
        assert ledger.connection.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 0
