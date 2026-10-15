"""what_bug_this_catches: copied generic math, invented human pairs or synthetic CI acceptance.

The imported fixture authors UNIT admission doubles; these are not human labels or a campaign.
"""

import hashlib
import json

import pytest
from test_calibration import unit_material

from llm_router_eval.shared.callledger.models import canonical_digest as h
from llm_router_eval.shared.evalkit.statistics import agreement, wilson_interval
from llm_router_eval.shared.routing.scoring.calibration import ObservationBundle, assess_judge
from llm_router_eval.shared.routing.scoring.canonical import (
    CANONICAL_PUBLICATION_SHA256,
    canonical_judge_statistics,
)
from llm_router_eval.shared.routing.scoring.labels import canonical_json


def test_actual_canonical_math_runs_2000_whole_prompt_draws_but_synthetic_never_permits():
    inputs = unit_material()
    execution = canonical_judge_statistics(**inputs)
    assert execution.status == "computed" and execution.reason is None
    assert (execution.group_count, execution.valid_draws) == (20, 2000)
    assert execution.rng_version == "Python3.12/random"
    assert execution.statistics.evidence_kind == "synthetic_control"
    assert execution.statistics.publication_sha256 == CANONICAL_PUBLICATION_SHA256
    assert execution.statistics.wilson_lower == wilson_interval(40, 40)[0]
    assert execution.statistics.prompt_cluster_lower == 1.0
    assert execution.statistics.kappa == 1.0
    assert canonical_judge_statistics(**inputs) == execution
    report = assess_judge(
        **inputs,
        statistics=execution.statistics,
        trusted_statistics_publications=frozenset({CANONICAL_PUBLICATION_SHA256}),
    )
    assert report["conservative_operating_lower"] == execution.statistics.wilson_lower
    assert report["status"] == "unvalidated" and not report["headline_rubric_permitted"]
    assert set(report["reasons"]) == {"observations_unverified", "canonical_statistics_unavailable"}


def test_actual_label_pairs_drive_kappa_and_cannot_be_replaced_by_match_only_labels():
    inputs = unit_material()
    data = inputs["bundle"].model_dump(mode="json")
    for row in data["predictions"]:
        if row["pair_id"] in {"unit-0-cheap", "unit-1-cheap", "unit-2-frontier"}:
            row["passed"] = not row["passed"]
    inputs["bundle"] = ObservationBundle.model_validate(data)
    execution = canonical_judge_statistics(**inputs)
    natural = sorted(
        (p for p in inputs["pairs"] if p.candidate_source != "corruption"), key=lambda p: p.pair_id
    )
    human = {r["pair_id"]: r["passed"] for r in json.loads(inputs["human_payload"])["labels"]}
    machine = {r.pair_id: r.passed for r in inputs["bundle"].predictions}
    expected = agreement(
        tuple(human[p.pair_id] for p in natural), tuple(machine[p.pair_id] for p in natural)
    )
    assert execution.statistics.kappa == expected["kappa"].value
    assert execution.statistics.wilson_lower == wilson_interval(37, 40)[0]
    assert execution.statistics.prompt_cluster_lower < 1.0
    assert execution.statistics.prompt_cluster_upper <= 1.0


def test_corrupted_pairs_cannot_raise_or_lower_natural_interval_or_draw_stream():
    inputs = unit_material()
    before = canonical_judge_statistics(**inputs)
    data = inputs["bundle"].model_dump(mode="json")
    for row in data["predictions"]:
        if row["pair_id"].endswith("corruption"):
            row["passed"] = not row["passed"]
    inputs["bundle"] = ObservationBundle.model_validate(data)
    assert canonical_judge_statistics(**inputs) == before


@pytest.mark.parametrize("failure", ["missing", "failed", "no_boolean"])
def test_incomplete_natural_population_returns_unavailable_without_intervals(failure):
    inputs = unit_material()
    data = inputs["bundle"].model_dump(mode="json")
    row = next(r for r in data["predictions"] if r["pair_id"] == "unit-0-cheap")
    if failure == "missing":
        data["predictions"].remove(row)
    elif failure == "failed":
        row.update(status="failed", probability=None, passed=None)
    else:
        row["passed"] = None
    inputs["bundle"] = ObservationBundle.model_validate(data)
    result = canonical_judge_statistics(**inputs)
    assert result.status == "unavailable" and result.reason == "natural_observations_incomplete"
    assert result.statistics is None and result.valid_draws == 0 and result.draws_sha256 is None


def test_source_binding_failures_still_refuse_before_statistics(monkeypatch):
    inputs = unit_material()
    called = []
    monkeypatch.setattr(
        "llm_router_eval.shared.routing.scoring.canonical.paired_group_resample",
        lambda *a, **kw: called.append(True),
    )
    inputs["config_sha256"] = h("wrong configuration")
    with pytest.raises(ValueError, match="observation_configuration"):
        canonical_judge_statistics(**inputs)
    assert not called


def test_same_draw_stream_uses_groups_and_paired_input_hash_captures_changed_truth():
    inputs = unit_material()
    before = canonical_judge_statistics(**inputs)
    payload = json.loads(inputs["human_payload"])
    row = next(r for r in payload["labels"] if r["pair_id"] == "unit-0-cheap")
    row["passed"] = True
    inputs["human_payload"] = canonical_json(payload)
    inputs["admission"] = inputs["admission"].model_copy(
        update={"label_file_sha256": hashlib.sha256(inputs["human_payload"]).hexdigest()}
    )
    after = canonical_judge_statistics(**inputs)
    assert after.draws_sha256 == before.draws_sha256
    assert after.paired_inputs_sha256 != before.paired_inputs_sha256
    assert after.statistics.input_sha256 != before.statistics.input_sha256
    assert after.statistics.wilson_lower < before.statistics.wilson_lower


def test_only_actual_canonical_functions_execute_and_no_math_is_forked(monkeypatch):
    import llm_router_eval.shared.routing.scoring.canonical as bridge
    from llm_router_eval.shared.evalkit import resampling, statistics

    assert bridge.wilson_interval is statistics.wilson_interval
    assert bridge.agreement is statistics.agreement
    assert bridge.paired_group_resample is resampling.paired_group_resample
    observed = []
    original = bridge.paired_group_resample

    def actual(rows, spec, callback):
        observed.append((rows, spec, callback))
        return original(rows, spec, callback)

    monkeypatch.setattr(bridge, "paired_group_resample", actual)
    result = bridge.canonical_judge_statistics(**unit_material())
    rows, spec, callback = observed[0]
    assert callback is statistics.accuracy
    assert spec.draws == 2000 and spec.seed == 20261014
    assert spec.min_support == 40 and spec.min_valid_draws == 2000
    assert sum(len(v) for v in rows.values()) == 40
    assert {r.evidence_origin for r in rows["judge"]} == {"synthetic"}
    assert result.statistics.evidence_kind == "synthetic_control"


def test_observed_self_flag_without_exact_trusted_receipt_remains_synthetic():
    inputs = unit_material()
    inputs["bundle"] = inputs["bundle"].model_copy(update={"evidence_kind": "observed"})
    execution = canonical_judge_statistics(**inputs)
    assert execution.statistics.evidence_kind == "synthetic_control"


def test_degenerate_actual_label_marginals_keep_null_kappa_and_fail_natural_gate():
    inputs = unit_material()
    payload = json.loads(inputs["human_payload"])
    for row in payload["labels"]:
        if not row["pair_id"].endswith("corruption"):
            row["passed"] = True
    inputs["human_payload"] = canonical_json(payload)
    inputs["admission"] = inputs["admission"].model_copy(
        update={"label_file_sha256": hashlib.sha256(inputs["human_payload"]).hexdigest()}
    )
    data = inputs["bundle"].model_dump(mode="json")
    for row in data["predictions"]:
        if not row["pair_id"].endswith("corruption"):
            row["passed"] = True
    inputs["bundle"] = ObservationBundle.model_validate(data)
    execution = canonical_judge_statistics(**inputs)
    assert (
        execution.statistics.kappa is None and execution.kappa_null_reason == "degenerate_marginals"
    )
    report = assess_judge(**inputs, statistics=execution.statistics)
    assert not report["quality_conditions_pass"] and not report["headline_rubric_permitted"]
