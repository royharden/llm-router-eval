"""Report-only references over retained observations; never executable routing policies."""

from collections import Counter
from decimal import Decimal
from random import Random
from types import MappingProxyType

from ..shared.callledger.models import canonical_digest
from ..shared.evalkit.models import Outcome
from ..shared.evalkit.reporting import metric_row, public_aggregate
from ..shared.evalkit.statistics import accuracy
from ..shared.routing.code_tier import can_handle
from .domain import AnswerAdapter, public_policy

SEED = 20261014
TIERS = ("code", "cheap", "frontier")
# Declared integer weights, never selected or tuned using these evaluation outcomes.
RANDOM_GRID = MappingProxyType(
    {
        "random_uniform": (1, 1, 1),
        "random_cheap_heavy": (1, 2, 1),
        "random_frontier_heavy": (1, 1, 2),
    }
)
ORACLE = "oracle_cheapest_passing"


def random_tier(item_id, eligible, weights):
    """Per-public-ID draw avoids iteration order, labels and private family metadata."""
    rng = Random(int(canonical_digest([SEED, item_id]), 16))
    bag = [
        tier
        for tier, weight in zip(TIERS, weights, strict=True)
        if tier in eligible
        for _ in range(weight)
    ]
    return bag[rng.randrange(len(bag))]


def oracle_tier(candidates):
    """Require all eligible observations/costs before claiming a minimum or no-pass result."""
    if any(row is None for row in candidates.values()):
        return None, "missing_observation"
    if any(row[1]["source_point_usd"] is None for row in candidates.values()):
        return None, "unknown_cost_rank"
    passing = [tier for tier, row in candidates.items() if row[0].correct is True]
    pool = passing or list(candidates)
    tier = min(pool, key=lambda t: (Decimal(candidates[t][1]["source_point_usd"]), TIERS.index(t)))
    return tier, None if passing else "no_passing_tier"


def _candidate(result, task, tier, source_rows, summarize_cost):
    method = "rules" if tier == "code" else "always_" + tier
    evaluated = result.comparison.methods.get(method)
    execution = result.source.executions.get((method, task))
    if evaluated is None or evaluated.status != "executed" or execution is None:
        return None
    execution = execution[0]
    if execution.tier != tier:
        return None
    matches = [row for row in evaluated.outcomes if row.item_id == task]
    if len(matches) != 1 or matches[0].status in {"unavailable", "stale", "not_run"}:
        return None
    if tier == "code":
        # A retained actual code execution has no model observation to consume.
        # Its local ledger span does not establish host-resource cost.
        return matches[0], summarize_cost([]), set()
    spans = set(execution.consumed_span_ids)
    retained = [row for row in source_rows if row["span_id"] in spans]
    if {row["span_id"] for row in retained} != spans:
        return None
    return matches[0], summarize_cost(retained), spans


def _unavailable(gold, name, origin):
    return Outcome.model_validate(
        dict(
            item_id=gold.item_id,
            group_id=gold.group_id,
            dataset=gold.dataset,
            split=gold.split,
            method=name,
            field=None,
            **{"class": None},
            eligibility="eligible",
            status="unavailable",
            correct=False,
            predicted=None,
            expected=AnswerAdapter.label(gold).answer,
            human_touched_prediction=False,
            evidence_origin=origin,
            issues=("reference_unavailable",),
            ledger_refs=(),
        )
    )


def build_references(result, view, summarize_cost, *, outcomes_sink=None):
    """Reuse canonical outcomes privately, then publish only fixed-population aggregates."""
    gold = tuple(
        g
        for g in view.split_records(result.comparison.split)
        if AnswerAdapter.label(g).kind != "rubric"
    )
    definition = dict(
        version="retained-reference-v1",
        seed=SEED,
        eligible_tiers=TIERS,
        random_integer_weights=dict(RANDOM_GRID),
        draw="per_public_id_sha256_seeded_randrange",
        oracle="cheapest passing; otherwise least-cost eligible and failed; tier-order ties",
        oracle_requires_complete_observation_and_cost_rank=True,
        source_method_configs={
            k: v.config_sha256
            for k, v in result.method_configs.items()
            if k in {"rules", "always_cheap", "always_frontier"}
        },
    )
    configs = {name: canonical_digest([name, definition]) for name in (*RANDOM_GRID, ORACLE)}
    output = dict(
        status="measured" if gold else "empty",
        case_N=len(gold),
        family_N=len({g.group_id for g in gold}),
        definition=definition,
        definition_sha256=canonical_digest(definition),
        rows=[],
        role="counterfactual report references; not runnable policies or tuning results",
        cost_scope="model_source_only",
        host_resource_cost_usd=None,
        timing="not_measured; no new provider call or policy execution",
    )
    if not gold:
        return output
    rows = result.source.safe_matrix_rows()
    population = tuple(g.item_id for g in gold)
    alias = view.manifest.publication_policy.allowed_value_sets["dataset_alias"][0]
    projection = public_policy(configs, alias=alias, origin=result.source.origin)
    all_candidates = {}
    for g in gold:
        eligible = (
            ("code", "cheap", "frontier")
            if can_handle(view.public_inputs[g.item_id]["prompt"])
            else ("cheap", "frontier")
        )
        all_candidates[g.item_id] = {
            tier: _candidate(result, g.item_id, tier, rows, summarize_cost) for tier in eligible
        }
    for name in configs:
        outcomes, spans, choices, reasons = [], set(), Counter(), Counter()
        for g in gold:
            candidates = all_candidates[g.item_id]
            if name == ORACLE:
                tier, reason = oracle_tier(candidates)
            else:
                tier = random_tier(g.item_id, candidates, RANDOM_GRID[name])
                reason = None if candidates[tier] is not None else "missing_observation"
            if reason is not None:
                reasons[reason] += 1
            if tier is None or candidates[tier] is None:
                outcomes.append(_unavailable(g, name, result.source.origin))
                continue
            original, _, consumed = candidates[tier]
            outcomes.append(
                Outcome.model_validate({**original.model_dump(mode="python"), "method": name})
            )
            spans.update(consumed)
            choices[tier] += 1
        missing = sum(row.status == "unavailable" for row in outcomes)
        if outcomes_sink is not None:
            outcomes_sink[name] = tuple(outcomes)
        metric = metric_row(
            tuple(outcomes),
            population,
            AnswerAdapter.metric_specs()[0],
            accuracy,
            method=name,
            dataset_alias=alias,
            split=result.comparison.split,
            config_ref=configs[name],
        )
        costs = summarize_cost(
            [row for row in rows if row["span_id"] in spans], complete=not missing
        )
        output["rows"].append(
            dict(
                reference=name,
                status="partial" if missing else "complete",
                metric=public_aggregate(metric, projection),
                source_cost=costs,
                cost_per_assigned_case_ratio=dict(
                    cost_usd=costs["source_point_usd"], cases=len(gold)
                ),
                cost_per_pass_ratio=dict(
                    cost_usd=costs["source_point_usd"], passes=metric.numerator
                ),
                cost_per_pass_undefined_reason="unknown_source_cost"
                if costs["source_point_usd"] is None
                else "zero_passes"
                if not metric.numerator
                else None,
                selected_tier_counts=dict(choices),
                unavailable_N=missing,
                reason_counts=dict(reasons),
            )
        )
    return output
