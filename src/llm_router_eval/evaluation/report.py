"""S3 aggregate assembly over canonical S2 metrics, without private per-item output."""

import json
from collections import Counter
from decimal import Decimal, localcontext
from statistics import median, quantiles

from ..shared.evalkit.reporting import metric_row, not_measured_row, public_aggregate
from ..shared.evalkit.statistics import accuracy
from .domain import AnswerAdapter
from .matrix import CELL_PURPOSES


def cost_summary(rows, *, complete=True):
    observed = [r for r in rows if r["span_id"] is not None]
    with localcontext() as context:
        context.prec = 150
        known = sum(
            (Decimal(r["source_cost_usd"]) for r in observed if r["source_cost_usd"] is not None),
            Decimal(0),
        )
        current = sum((Decimal(r["current_external_cost_usd"]) for r in rows), Decimal(0))
    unknown = sum(r["source_cost_usd"] is None for r in observed)
    return dict(
        observed_source_cells=len(observed),
        source_known_subtotal_usd=format(known, "f"),
        source_unknown_cells=unknown,
        source_lower_usd=format(known, "f"),
        source_upper_usd=None if unknown or not complete else format(known, "f"),
        source_point_usd=None if unknown or not complete else format(known, "f"),
        source_coverage="complete" if complete else "incomplete_or_not_run",
        current_external_cost_usd=format(current, "f"),
    )


def timing_summary(values):
    return dict(
        n=len(values),
        p50_ms=median(values) if values else None,
        p95_ms=quantiles(values, n=100, method="inclusive")[94] if len(values) > 1 else None,
        p95_method="stdlib_inclusive_linear",
        mode="local_offline_only",
    )


def build_report(result, view, ledger_totals):
    """Only explicit aggregate values enter this report. No prompts, labels or answers."""
    comparison = result.comparison
    gold = view.split_records(comparison.split)
    eligible_ids = tuple(g.item_id for g in gold if AnswerAdapter.label(g).kind != "rubric")
    spec = AnswerAdapter.metric_specs()[0]
    rows = []
    costs = []
    source_rows = result.source.safe_matrix_rows()
    expected_cells = comparison.corpus_count * len(CELL_PURPOSES)
    collection_complete = len(source_rows) == expected_cells
    execution_counts = Counter(policy for policy, _ in result.source.executions)
    alias = view.manifest.publication_policy.allowed_value_sets["dataset_alias"][0]
    for name, method in comparison.methods.items():
        config = result.method_configs[name]
        kwargs = dict(
            method=name,
            dataset_alias=alias,
            split=comparison.split,
            config_ref=config.config_sha256,
        )
        metric = (
            not_measured_row(comparison.frozen_item_ids, eligible_ids, spec, **kwargs)
            if method.status == "not_run"
            else metric_row(method.outcomes, comparison.frozen_item_ids, spec, accuracy, **kwargs)
        )
        rows.append(public_aggregate(metric, view.manifest.publication_policy))
        spans = {
            span
            for (policy, _), (execution, _) in result.source.executions.items()
            if policy == name
            for span in execution.consumed_span_ids
        }
        summary = cost_summary(
            [r for r in source_rows if r["span_id"] in spans],
            complete=method.status == "executed"
            and execution_counts[name] == comparison.corpus_count,
        )
        passes = metric.numerator
        costs.append(
            dict(
                method=name,
                status=method.status,
                **summary,
                cost_per_pass_ratio=dict(cost_usd=summary["source_point_usd"], passes=passes),
                cost_per_pass_undefined_reason="not_run"
                if method.status == "not_run"
                else "unknown_source_cost"
                if summary["source_point_usd"] is None
                else "zero_passes"
                if not passes
                else None,
            )
        )
    return dict(
        schema_version=1,
        kind="s3-offline-aggregate",
        evidence_origin=result.source.origin,
        dataset_evidence=view.evidence_status,
        split=comparison.split,
        corpus_N=comparison.corpus_count,
        deterministic_eligible_N=len(eligible_ids),
        rubric_unscored_N=len(gold) - len(eligible_ids),
        manifest_sha256=view.manifest_sha256,
        input_dataset_sha256=view.source_payload_sha256,
        source_book_sha256=result.source.source_book_sha256,
        metrics=rows,
        collection=dict(
            status="complete" if collection_complete else "incomplete",
            expected_cells=expected_cells,
            retained_cells=len(source_rows),
            uncollected_cells=expected_cells - len(source_rows),
        ),
        source_status_counts=dict(Counter(r["status"] for r in source_rows)),
        collection_cost=cost_summary(source_rows, complete=collection_complete),
        counterfactual_policy_consumption=costs,
        local_lookup_timing=timing_summary(
            [r["lookup_latency_ms"] for r in source_rows if r["lookup_latency_ms"] is not None]
        ),
        local_policy_timing=timing_summary(
            [elapsed for _, elapsed in result.source.executions.values()]
        ),
        ledger=dict(
            dispatched_attempts=ledger_totals["dispatched_attempts"],
            known_spend_usd=ledger_totals["known_spend_usd"],
            unknown_liability_usd=ledger_totals["unknown_liability_usd"],
            total_exposure_usd=ledger_totals["total_exposure_usd"],
        ),
        cost_semantics=(
            "Authored source costs are synthetic counterfactual controls, not model prices "
            "or observed invoices. Policy consumption reuses the same collected sources; "
            "do not sum it as collection spend."
        ),
        timing_semantics=(
            "Local fixture lookup and policy execution only; model latency, TTFT "
            "and source timing are not measured."
        ),
        host_resource_cost_usd=None,
        model_usage="unknown",
        intervals="not_requested_for_authored_controls",
        acceptance=(
            "synthetic_implementation_only; no benchmark, calibration, heldout "
            "or release acceptance"
        ),
    )


def render_json(report):
    return (
        json.dumps(report, sort_keys=True, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    ).encode()


def render_markdown(report):
    lines = [
        "# Authored offline routing controls",
        "",
        "Synthetic implementation evidence. No model quality, real pricing or heldout claim.",
        "",
        "| Policy | Passed / eligible | Assigned | Unscored rubric | Missing/error | Stale |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in report["metrics"]:
        passed = (
            "not run"
            if row.get("null_reason") == "not_run"
            else f"{row['numerator']} / {row['denominator']}"
        )
        lines.append(
            f"| {row['method']} | {passed} | {row['corpus_N']} | "
            f"{row['corpus_N'] - row['eligible_N']} | {row['error_N']} | {row['stale_N']} |"
        )
    lines += [
        "",
        "Current external spend: $"
        + report["ledger"]["known_spend_usd"]
        + "; actual outbound attempts: "
        + str(report["ledger"]["dispatched_attempts"])
        + ".",
        "",
        report["cost_semantics"],
        "",
        report["timing_semantics"],
        "",
        "Rubric judgments, independent labels, real captures, licensed corpus, "
        "charts and provider transport remain pending.",
        "",
    ]
    return "\n".join(lines).encode()
