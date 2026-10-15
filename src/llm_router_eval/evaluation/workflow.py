"""Explicit-input authored offline workflow. No network, SDK, credentials or raw archive."""

import hashlib
import json
from pathlib import Path
from uuid import uuid4

from ..data.corpus import load_dataset
from ..shared.callledger import Ledger, LedgerError
from ..shared.callledger.models import (
    BudgetAuthority,
    ChildBound,
    Limits,
    OperationPlan,
    canonical_digest,
)
from ..shared.callledger.pricing import FundingEvidence, PricePolicy, PriceProvenance, PriceSnapshot
from ..shared.providers.adapters import decode_outcome
from ..shared.routing.context import RouteInput
from ..shared.routing.policies import Policy
from .control_book import build_control_book
from .domain import prepare_view
from .engine import MatrixSource, policy_configuration_sha, run_comparison
from .report import build_report, render_json, render_markdown

POLICIES = (
    "always_cheap",
    "always_frontier",
    "rules",
    "classifier",
    "jev_gate",
    "cascade_jev",
    "cascade_llm",
)
SCENARIOS = ("normal", "code", "cheap", "escalation", "malformed", "budget", "unknown_cost")


def virtual_budget_refusal(target, authority, limits, context):
    """Actual Ledger admission in simulation mode; never a provider Result or paid request."""
    authority = authority.model_copy(
        update={"authority_id": "virtual-funding-control", "max_outbound_attempts": 1}
    )
    limits = limits.model_copy(update={"max_outbound_attempts": 1})
    context = context.model_copy(
        update={"authority_id": authority.authority_id, "operation_id": "virtual-funding-refusal"}
    )
    policy = PricePolicy(
        schema_version=1,
        provider="synthetic",
        model="virtual-budget-model",
        context_sha256=canonical_digest("virtual-control"),
        cache_mode="flat_inclusive",
        input_usd_per_million=None,
        cache_read_usd_per_million=None,
        cache_write_usd_per_million=None,
        output_usd_per_million=None,
        fee_usd="0.01",
    )
    snapshot = PriceSnapshot(
        schema_version=1,
        provenance=PriceProvenance(
            origin="synthetic",
            source_url=None,
            retrieved_at="2026-10-14T00:00:00Z",
            official_verified_at=None,
            source_commit=None,
        ),
        policies=[policy],
        data_sha256=canonical_digest([policy.model_dump(mode="json")]),
    )
    bound = FundingEvidence(
        schema_version=1,
        snapshot_sha256=snapshot.snapshot_sha256,
        policy_sha256=policy.policy_sha256,
        provider_config_sha256=context.config_sha256,
        model=policy.model,
        request_shape_limit_sha256=context.template_sha256,
        input_token_maximum=0,
        output_token_maximum=None,
        bound_kind="synthetic_fixture",
        proof_sha256=canonical_digest("virtual-funding-only"),
    )
    child = ChildBound(
        child_id="virtual-child",
        purpose=context.purpose,
        provider_config_sha256=bound.provider_config_sha256,
        model=policy.model,
        bound_evidence_sha256=bound.evidence_sha256,
        max_attempts=1,
        max_repairs=0,
        input_token_maximum=0,
        output_token_maximum=None,
        fee_maximum_usd="0.01",
        reserved_usd_per_attempt="0.01",
        request_shape_limit_sha256=bound.request_shape_limit_sha256,
        conditional=False,
    )
    plan = OperationPlan(
        schema_version=1,
        operation_id=context.operation_id,
        authority_id=authority.authority_id,
        context_sha256=canonical_digest(context.model_dump(mode="json")),
        children=[child],
    )
    with Ledger(
        target / "virtual-funding.sqlite3",
        authority=authority,
        price_snapshot=snapshot,
        trusted_bounds=[bound],
    ) as ledger:
        try:
            ledger.admit_operation(plan, context=context, limits=limits, mode="simulation")
        except LedgerError as error:
            if str(error) != "budget_rejected":
                raise
        else:
            raise ValueError("virtual_refusal_not_observed")
        totals = ledger.totals()
    return dict(
        kind="virtual_funding_control",
        status="budget_rejected",
        mode="simulation",
        requested_reservation_usd="0.01",
        cap_usd="0",
        outbound_attempts=totals["dispatched_attempts"],
        actual_model_requests=0,
        provider_result_created=False,
        real_provider_price_proof=False,
    )


def offline_authority():
    authority = BudgetAuthority(
        schema_version=1,
        authority_id="authored-offline-authority",
        scope_sha256=canonical_digest("authored-DEV-only-zero-dispatch"),
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


def run_offline(tasks_path, vectors_path, output_dir, *, scenario="normal"):
    if scenario not in SCENARIOS:
        raise ValueError("offline_scenario")
    target = Path(output_dir)
    if target.exists():
        raise ValueError("output_exists")
    tasks_path, vectors_path = Path(tasks_path), Path(vectors_path)
    if tasks_path.stat().st_size > 8_000_000 or vectors_path.stat().st_size > 2_000_000:
        raise ValueError("input_size")
    payload = tasks_path.read_bytes()
    vectors = vectors_path.read_bytes()
    dataset = load_dataset(payload)
    if dataset.evidence_status != "synthetic_implementation":
        raise ValueError("authored_DEV_only")
    if len(dataset.tasks) > 12:
        raise ValueError("authored_control_limit")
    authority, limits = offline_authority()
    costs = {}
    if scenario == "unknown_cost":
        costs = {
            (t.task_id, cell): None
            for t in dataset.tasks
            for cell in ("cheap", "frontier", "classifier", "gate", "check_jev", "check_llm")
        }
    book_options = dict(
        items=[RouteInput.from_data_view(t.routing_view()) for t in dataset.tasks],
        vectors_payload=vectors,
        authority=authority,
        limits=limits,
        run_id="offline-" + uuid4().hex,
        cost_overrides=costs,
    )
    book = build_control_book(**book_options)
    if scenario == "malformed":
        overrides = {}
        for spec in book.specs:
            cell = next(c for c in spec.cells if c.name == "classifier")
            model = cell.provider_config.models[cell.prepared.request.model].reported_models[0]
            body = json.dumps(
                dict(
                    model=model,
                    status="completed",
                    output=[
                        dict(
                            type="message",
                            content=[dict(type="output_text", text='{"tier":"INVALID"}')],
                        )
                    ],
                )
            ).encode()
            outcome = decode_outcome(
                body,
                prepared=cell.prepared,
                config=cell.provider_config,
                max_bytes=limits.max_response_bytes,
            )
            if outcome.status != "invalid_output":
                raise ValueError("malformed_control_not_refused")
            overrides[(spec.item.prompt_id, "classifier")] = outcome
        book = build_control_book(**book_options, outcome_overrides=overrides)
    policies = tuple(Policy(n) for n in POLICIES)
    configs = {p.name: policy_configuration_sha(p, book.specs[0].config.sha256) for p in policies}
    view = prepare_view(
        payload, frozen_at="2026-10-14T00:00:00Z", method_configs=configs, all_synthetic_dev=True
    )
    # Inputs and all source bindings validated before a fresh output directory is claimed.
    target.mkdir(parents=False, exist_ok=False)
    with Ledger(
        target / "accounting.sqlite3", authority=authority, trusted_requests=book.request_evidence
    ) as ledger:
        source = MatrixSource(
            specs=book.specs, records=book.records, ledger=ledger, budget=authority
        )
        comparison = run_comparison(view, source, policies)
        report = build_report(comparison, view, ledger.totals())
        report["scenario"] = scenario
        if scenario == "budget":
            report["virtual_budget_control"] = virtual_budget_refusal(
                target, authority, limits, book.specs[0].root_context
            )
        report["vectors_sha256"] = hashlib.sha256(vectors).hexdigest()
        report["scenario_observations"] = dict(
            code_executions=sum(e.tier == "code" for e, _ in source.executions.values()),
            cheap_selections=sum(e.tier == "cheap" for e, _ in source.executions.values()),
            frontier_selections=sum(e.tier == "frontier" for e, _ in source.executions.values()),
            unvalidated_checker_escalations=sum(
                "checker_unvalidated" in e.reasons for e, _ in source.executions.values()
            ),
            malformed_classifier_escalations=sum(
                "classifier_invalid" in e.reasons for e, _ in source.executions.values()
            ),
        )
        (target / "report.json").write_bytes(render_json(report))
        (target / "report.md").write_bytes(render_markdown(report))
    return report
