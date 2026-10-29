"""S3 aggregate assembly over canonical S2 metrics, without private per-item output."""

import json
from collections import Counter
from decimal import Decimal, localcontext
from statistics import median, quantiles

from ..shared.evalkit.reporting import metric_row, not_measured_row, public_aggregate
from ..shared.evalkit.statistics import accuracy
from .domain import AnswerAdapter
from .matrix import CELL_PURPOSES
from .quality_intervals import quality_intervals
from .references import build_references

COHORT_KINDS = ("gsm8k", "mmlu", "code", "exact", "rubric")


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


def _consumed_cost(result, method, expected_ids, source_rows, status):
    if status == "not_run":
        return cost_summary([], complete=False)
    selected = set(expected_ids)
    executions = [
        execution
        for (policy, task), (execution, _) in result.source.executions.items()
        if policy == method and task in selected
    ]
    spans = {span for execution in executions for span in execution.consumed_span_ids}
    return cost_summary(
        [row for row in source_rows if row["span_id"] in spans],
        complete=status == "executed" and len(executions) == len(expected_ids),
    )


def _cost_reason(summary, status, passes, eligible_count):
    if status == "not_run":
        return "not_run"
    if not eligible_count:
        return "no_eligible_cases"
    if summary["source_point_usd"] is None:
        return "unknown_source_cost"
    return "zero_passes" if not passes else None


def _cohort(result, view, gold, spec, source_rows):
    """Domain-only fixed population wrapper; canonical ports still own every metric."""
    expected = tuple(record.item_id for record in gold)
    eligible = tuple(
        record.item_id for record in gold if AnswerAdapter.label(record).kind != "rubric"
    )
    output = dict(
        status="measured" if expected else "empty",
        case_N=len(expected),
        eligible_N=len(eligible),
        unscored_N=len(expected) - len(eligible),
        family_N=len({record.group_id for record in gold}),
        metrics=[],
        source_costs=[],
        cost_scope="model_source_only",
        host_resource_cost_usd=None,
    )
    if not expected:
        return output
    alias = view.manifest.publication_policy.allowed_value_sets["dataset_alias"][0]
    selected = set(expected)
    for name, method in result.comparison.methods.items():
        kwargs = dict(
            method=name,
            dataset_alias=alias,
            split=result.comparison.split,
            config_ref=result.method_configs[name].config_sha256,
        )
        metric = (
            not_measured_row(expected, eligible, spec, **kwargs)
            if method.status == "not_run"
            else metric_row(
                tuple(row for row in method.outcomes if row.item_id in selected),
                expected,
                spec,
                accuracy,
                **kwargs,
            )
        )
        output["metrics"].append(public_aggregate(metric, view.manifest.publication_policy))
        summary = _consumed_cost(result, name, expected, source_rows, method.status)
        output["source_costs"].append(
            dict(
                method=name,
                status=method.status,
                **summary,
                cost_per_assigned_case_ratio=dict(
                    cost_usd=summary["source_point_usd"], cases=len(expected)
                ),
                cost_per_pass_ratio=dict(
                    cost_usd=summary["source_point_usd"], passes=metric.numerator
                ),
                cost_per_pass_undefined_reason=_cost_reason(
                    summary, method.status, metric.numerator, len(eligible)
                ),
            )
        )
    return output


def build_report(result, view, ledger_totals, *, include_intervals=False):
    """Only explicit aggregate values enter this report. No prompts, labels or answers."""
    comparison = result.comparison
    recorded = result.source.origin == "recorded_replay"
    gold = view.split_records(comparison.split)
    eligible_ids = tuple(g.item_id for g in gold if AnswerAdapter.label(g).kind != "rubric")
    spec = AnswerAdapter.metric_specs()[0]
    private_references = {} if include_intervals else None
    references = build_references(result, view, cost_summary, outcomes_sink=private_references)
    sampling = (
        quality_intervals(result, view, private_references)
        if include_intervals
        else dict(status="not_requested", methods={})
    )
    rows = []
    costs = []
    source_rows = result.source.safe_matrix_rows()
    expected_cells = comparison.corpus_count * len(CELL_PURPOSES)
    collection_complete = len(source_rows) == expected_cells
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
        summary = _consumed_cost(
            result, name, comparison.frozen_item_ids, source_rows, method.status
        )
        passes = metric.numerator
        costs.append(
            dict(
                method=name,
                status=method.status,
                **summary,
                cost_per_pass_ratio=dict(cost_usd=summary["source_point_usd"], passes=passes),
                cost_per_pass_undefined_reason=_cost_reason(
                    summary, method.status, passes, len(eligible_ids)
                ),
            )
        )
    labels = {record.item_id: AnswerAdapter.label(record).kind for record in gold}
    cohorts = dict(
        deterministic=_cohort(
            result,
            view,
            tuple(record for record in gold if labels[record.item_id] != "rubric"),
            spec,
            source_rows,
        ),
        kinds=[
            dict(
                kind=kind,
                **_cohort(
                    result,
                    view,
                    tuple(record for record in gold if labels[record.item_id] == kind),
                    spec,
                    source_rows,
                ),
            )
            for kind in COHORT_KINDS
            if kind in set(labels.values())
        ],
        absent_kinds=[kind for kind in COHORT_KINDS if kind not in set(labels.values())],
        cost_population=(
            "Each cohort cost uses exactly its assigned cases; deterministic quality and "
            "source-cost comparison share the same eligible cases."
        ),
        identifier_policy=(
            "Closed kind labels and family counts only; no raw prompt, source, family or "
            "task identifiers."
        ),
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
        cohorts=cohorts,
        references=references,
        sampling=sampling,
        local_lookup_timing=timing_summary(
            [r["lookup_latency_ms"] for r in source_rows if r["lookup_latency_ms"] is not None]
        ),
        local_policy_timing=timing_summary(
            [elapsed for _, elapsed in result.source.executions.values()]
        ),
        local_policy_by_method_timing={
            name: timing_summary(
                [
                    elapsed
                    for (method, task), (_, elapsed) in result.source.executions.items()
                    if method == name and task in set(eligible_ids)
                ]
            )
            for name in comparison.methods
        },
        ledger=dict(
            dispatched_attempts=ledger_totals["dispatched_attempts"],
            known_spend_usd=ledger_totals["known_spend_usd"],
            unknown_liability_usd=ledger_totals["unknown_liability_usd"],
            total_exposure_usd=ledger_totals["total_exposure_usd"],
        ),
        cost_semantics=(
            "Retained recorded source-cost metadata, not a new invoice measurement. "
            "Replay external model charge is zero; unknown source costs stay unknown. "
            "Counterfactual policy consumption must not be summed as collection spend."
            if recorded
            else "Authored source costs are synthetic counterfactual controls, not model prices "
            "or observed invoices. Policy consumption reuses the same collected sources; "
            "do not sum it as collection spend."
        ),
        timing_semantics=(
            "Local fixture lookup and policy execution only; model latency, TTFT "
            "and source timing are not measured."
        ),
        host_resource_cost_usd=None,
        model_usage="unknown",
        intervals="canonical_family_bootstrap"
        if include_intervals
        else "not_requested_for_recorded_replay"
        if recorded
        else "not_requested_for_authored_controls",
        acceptance=(
            "admitted_recorded_replay; this report alone establishes no model quality, "
            "calibration, heldout independence or release acceptance"
            if recorded
            else "synthetic_implementation_only; no benchmark, calibration, heldout "
            "or release acceptance"
        ),
    )


def render_json(report):
    return (
        json.dumps(report, sort_keys=True, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    ).encode()


def render_markdown(report):
    recorded = report["evidence_origin"] == "recorded_replay"
    lines = [
        "# Admitted recorded routing replay" if recorded else "# Authored offline routing controls",
        "",
        (
            "Recorded-source replay with caller-supplied admissions. No new model calls; "
            "quality, calibration and independent heldout acceptance remain separate."
            if recorded
            else (
                "Synthetic implementation evidence. No model quality, real pricing "
                "or heldout claim."
            )
        ),
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
        "## Deterministic comparison population",
        "",
        "Source-cost totals below use the same deterministic cases as the eligible accuracy rows.",
        "| Policy | Cases | Counterfactual source USD |",
        "|---|---:|---:|",
    ]
    for row in report["cohorts"]["deterministic"]["source_costs"]:
        amount = "unknown" if row["source_point_usd"] is None else row["source_point_usd"]
        lines.append(
            f"| {row['method']} | {row['cost_per_assigned_case_ratio']['cases']} | {amount} |"
        )
    if report["cohorts"]["deterministic"]["status"] == "empty":
        lines.append("No deterministic cases; no cost/accuracy point is inferred.")
    lines += [
        "",
        "## Kind coverage",
        "",
        "| Kind | Assigned | Eligible | Unscored | Families |",
        "|---|---:|---:|---:|---:|",
    ]
    for cohort in report["cohorts"]["kinds"]:
        lines.append(
            f"| {cohort['kind']} | {cohort['case_N']} | {cohort['eligible_N']} | "
            f"{cohort['unscored_N']} | {cohort['family_N']} |"
        )
    lines += [
        "",
        "## Counterfactual references",
        "",
        "Fixed seed/grid; oracle uses scoring only after routing. No runnable oracle or tuning.",
        "Model-source costs only; host cost and reference/model latency remain unknown.",
        "| Reference | Passed / eligible | Missing | Source USD |",
        "|---|---:|---:|---:|",
    ]
    for row in report["references"]["rows"]:
        metric = row["metric"]
        amount = row["source_cost"]["source_point_usd"]
        lines.append(
            f"| {row['reference']} | {metric['numerator']} / {metric['denominator']} | "
            f"{row['unavailable_N']} | {'unknown' if amount is None else amount} |"
        )
    if report["references"]["status"] == "empty":
        lines.append("No deterministic cases; no reference point is inferred.")
    if report["sampling"]["status"] == "computed":
        lines += [
            "",
            "## Authored family sampling",
            "",
            "95% canonical paired-family intervals; 2,000 shared draws, seed 20261014. "
            "At least two families are required. These controls do not establish model quality.",
            "| Method | Lower | Upper | Undefined reason |",
            "|---|---:|---:|---|",
        ]
        for name, estimate in report["sampling"]["methods"].items():
            interval = estimate["interval"]
            low = "unavailable" if interval is None else str(interval["low"])
            high = "unavailable" if interval is None else str(interval["high"])
            lines.append(f"| {name} | {low} | {high} | {estimate['null_reason'] or ''} |")
    if "figures" in report:
        lines += [
            "",
            "Figures: [PNG](quality-cost.png), [SVG](quality-cost.svg), "
            "[exact figure table](figure-data.json).",
        ]
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
        "and provider transport remain pending.",
        "",
    ]
    return "\n".join(lines).encode()
