"""Hand-count domain controls; no generic statistics or real human/model data."""

import hashlib
import json

import pytest

from llm_router_eval.shared.callledger.models import canonical_digest as h
from llm_router_eval.shared.routing.scoring.calibration import (
    JudgeStatisticsEvidence,
    ObservationBundle,
    Prediction,
    assess_checker,
    assess_judge,
    judge_counts,
)
from llm_router_eval.shared.routing.scoring.labels import (
    HumanAdmission,
    HumanLabelFile,
    PairSpec,
    blank_template,
    canonical_json,
)


@pytest.fixture(autouse=True)
def bug_metadata(request):
    request.node.user_properties.append(("what_bug_this_catches", request.node.function.__doc__))


def unit_material():
    """Ephemeral human/capture admission test doubles, never actual source receipts/Gold."""
    pairs = []
    truth = {}
    predictions = []
    for prompt in range(20):
        for source in ["cheap", "frontier", "corruption"]:
            pair_id = f"unit-{prompt}-{source}"
            response = h(["UNIT synthetic response", pair_id])
            pair = PairSpec(
                pair_id=pair_id,
                prompt_id=f"unit-prompt-{prompt}",
                candidate_source=source,
                response_sha256=response,
            )
            pairs.append(pair)
            truth[pair_id] = source == "frontier"
            predictions.append(
                Prediction(
                    pair_id=pair_id,
                    answer_response_sha256=response,
                    status="ok",
                    probability=0.9 if truth[pair_id] else 0.1,
                    passed=truth[pair_id],
                    capture_sha256=h(["UNIT capture double", pair_id]),
                )
            )
    blind = json.loads(blank_template(pairs))["blind_manifest_sha256"]
    labels = HumanLabelFile(
        schema_version=1,
        evidence_kind="blinded_human",
        human_id="UNIT-HUMAN-ADMISSION-DOUBLE",
        blind_manifest_sha256=blind,
        labels=[dict(pair_id=p.pair_id, passed=truth[p.pair_id]) for p in pairs],
    )
    payload = canonical_json(labels.model_dump(mode="json"))
    admission = HumanAdmission(
        label_file_sha256=hashlib.sha256(payload).hexdigest(),
        blind_manifest_sha256=blind,
        pair_manifest_sha256=h([p.model_dump(mode="json") for p in pairs]),
        human_id=labels.human_id,
        owner_receipt_sha256=h("UNIT OWNER DOUBLE"),
        expected_pair_ids=list(truth),
        excluded_agent_ids=["UNIT-AGENT"],
    )
    bundle = ObservationBundle(
        evidence_kind="synthetic_control",
        split="dev",
        config_sha256=h("UNIT CONFIG"),
        producer_receipt_sha256=h("UNIT observation double"),
        predictions=predictions,
    )
    return dict(
        pairs=pairs,
        human_payload=payload,
        admission=admission,
        bundle=bundle,
        config_sha256=bundle.config_sha256,
    )


def toy_bounds(material, **changes):
    """Supplied hand-count bounds, not executed Wilson/bootstrap/kappa calculations."""
    _, request, _ = judge_counts(**material)
    return JudgeStatisticsEvidence.model_validate(
        dict(
            evidence_kind="synthetic_control",
            input_sha256=h(request.model_dump(mode="json")),
            publication_sha256=None,
            replicates=2000,
            seed=20261014,
            confidence="two_sided_95",
            wilson_lower=0.9,
            wilson_upper=1.0,
            prompt_cluster_lower=0.85,
            prompt_cluster_upper=1.0,
            kappa=None,
        )
        | changes
    )


def test_hand_count_checker_thresholds_and_denominators_do_not_grant_real_permit():
    """Synthetic probabilities become real calibration or false-accept denominator is wrong."""
    material = unit_material()
    bundle = material["bundle"]
    data = bundle.model_dump(mode="json")
    for row in data["predictions"][:2]:
        if not row["passed"]:
            row["probability"] = 0.6
    # Two extra false accepts at0.5; only the20 correct frontier responses at0.7.
    data["predictions"][2]["probability"] = 0.6
    material["bundle"] = ObservationBundle.model_validate(data)
    report = assess_checker(**material, backend="jev")
    low, mid, _ = report.thresholds
    assert (low.accepted, low.false_accepts, mid.accepted, mid.false_accepts) == (22, 2, 20, 0)
    assert low.false_accepts_per_accepted == pytest.approx(2 / 22)
    assert low.false_accepts_per_human_fail == 0.05
    assert not low.qualifies and mid.qualifies and report.selected_threshold == 0.7
    assert report.status == "unvalidated" and report.reasons == ["observations_unverified"]
    assert report.as_policy_calibration(receipt_sha256=h("UNIT no operating grant")) is None


def test_no_accepts_or_human_fails_have_null_rates_and_fail_closed():
    """Zero accepted denominators turn into zero false-accept rates and a valid gate."""
    material = unit_material()
    bundle = material["bundle"].model_dump(mode="json")
    for row in bundle["predictions"]:
        row["probability"] = 0.0
    material["bundle"] = ObservationBundle.model_validate(bundle)
    report = assess_checker(**material, backend="llm")
    assert report.selected_threshold is None and all(
        t.false_accepts_per_accepted is None for t in report.thresholds
    )
    assert all(t.accepted == 0 and not t.qualifies for t in report.thresholds)
    human = json.loads(material["human_payload"])
    for row in human["labels"]:
        row["passed"] = True
    material["human_payload"] = canonical_json(human)
    material["admission"] = material["admission"].model_copy(
        update={"label_file_sha256": hashlib.sha256(material["human_payload"]).hexdigest()}
    )
    no_fail = assess_checker(**material, backend="llm")
    assert no_fail.human_fail_cases == 0
    assert all(
        t.false_accepts_per_human_fail is None and not t.qualifies for t in no_fail.thresholds
    )


def test_judge_natural_and_corrupted_cohorts_cannot_inflate_each_other():
    """Easy corrupted cases inflate natural agreement or its human-fail denominator."""
    material = unit_material()
    bundle = material["bundle"].model_dump(mode="json")
    for row in bundle["predictions"]:
        if row["pair_id"].endswith("corruption"):
            row["passed"] = True
    material["bundle"] = ObservationBundle.model_validate(bundle)
    cohorts, request, source = judge_counts(**material)
    natural, corrupt = cohorts["natural"], cohorts["corrupted"]
    assert (natural["assigned"], natural["observed"], natural["human_fail_cases"]) == (40, 40, 20)
    assert natural["agreement"] == 1.0 and natural["false_pass_per_human_fail"] == 0.0
    assert (
        corrupt["assigned"] == 20
        and corrupt["agreement"] == 0.0
        and corrupt["confusion"]["false_pass"] == 20
    )
    assert len(request.natural_prompt_matches) == 20 and all(
        len(v) == 2 for v in request.natural_prompt_matches.values()
    )
    assert request.replicates == 2000 and request.seed == 20261014 and not source


def test_synthetic_ci_cannot_validate_and_conservative_minimum_is_explicit():
    """Hand-supplied bounds masquerade as canonical execution or the larger lower bound is used."""
    material = unit_material()
    statistics = toy_bounds(material)
    report = assess_judge(**material, statistics=statistics)
    assert report["conservative_operating_lower"] == 0.85
    assert report["quality_conditions_pass"] and report["status"] == "unvalidated"
    assert not report["headline_rubric_permitted"]
    assert "canonical_statistics_unavailable" in report["reasons"]
    assert report["statistics"]["kappa"] is None
    lower = assess_judge(**material, statistics=toy_bounds(material, prompt_cluster_lower=0.79))
    assert not lower["quality_conditions_pass"]


def test_unpublished_canonical_statistics_default_is_unavailable():
    """Missing canonical S2 source creates made-up confidence/kappa numbers."""
    report = assess_judge(**unit_material())
    assert report["statistics"] is None and report["conservative_operating_lower"] is None
    assert report["status"] == "unvalidated" and not report["headline_rubric_permitted"]


def test_false_pass_uses_natural_human_fail_not_all_sixty_pairs():
    """False-pass rate divides by all pairs instead of actual natural human-fail examples."""
    material = unit_material()
    data = material["bundle"].model_dump(mode="json")
    for row in data["predictions"]:
        if row["pair_id"] in ["unit-0-cheap", "unit-1-cheap"]:
            row["passed"] = True
    material["bundle"] = ObservationBundle.model_validate(data)
    report = assess_judge(**material, statistics=toy_bounds(material))
    assert report["cohorts"]["natural"]["confusion"]["false_pass"] == 2
    assert report["cohorts"]["natural"]["false_pass_per_human_fail"] == 0.1
    assert not report["quality_conditions_pass"]


@pytest.mark.parametrize(
    "mutation", ["missing", "failed", "wrong_response", "duplicate", "wrong_config", "heldout"]
)
def test_partial_or_mismatched_observations_never_become_complete_calibration(mutation):
    """Missing/failed/wrong-item/heldout predictions silently pass operating calibration."""
    material = unit_material()
    data = material["bundle"].model_dump(mode="json")
    if mutation == "missing":
        data["predictions"].pop()
    elif mutation == "failed":
        data["predictions"][0].update(status="failed", probability=None, passed=None)
    elif mutation == "wrong_response":
        data["predictions"][0]["answer_response_sha256"] = h("WRONG")
    elif mutation == "duplicate":
        data["predictions"].append(data["predictions"][0])
    elif mutation == "wrong_config":
        data["config_sha256"] = h("WRONG")
    else:
        data["split"] = "heldout"
    material["bundle"] = ObservationBundle.model_validate(data)
    if mutation in ["missing", "failed"]:
        report = assess_checker(**material, backend="jev")
        assert (
            report.missing == 1
            and report.selected_threshold is None
            and report.status == "unvalidated"
        )
        judge = assess_judge(**material)
        assert "incomplete_observations" in judge["reasons"]
    else:
        with pytest.raises(ValueError):
            assess_checker(**material, backend="jev")


def test_nominal_stats_contract_rejects_wrong_replicates_and_input_hash():
    """A1000-replicate fork or intervals from different natural matches reuse the gate."""
    material = unit_material()
    with pytest.raises(ValueError):
        toy_bounds(material, replicates=1000)
    with pytest.raises(ValueError, match="statistics_input_binding"):
        assess_judge(**material, statistics=toy_bounds(material, input_sha256=h("WRONG")))


def test_inclusive_approved_lower_and_falsepass_boundaries_remain_inclusive():
    """Transcription changes >=.80/<=.05 into stricter comparisons without authority."""
    material = unit_material()
    data = material["bundle"].model_dump(mode="json")
    data["predictions"][0]["passed"] = True
    material["bundle"] = ObservationBundle.model_validate(data)
    report = assess_judge(
        **material, statistics=toy_bounds(material, wilson_lower=0.8, prompt_cluster_lower=0.8)
    )
    assert report["cohorts"]["natural"]["false_pass_per_human_fail"] == 0.05
    assert report["quality_conditions_pass"] and report["status"] == "unvalidated"


def test_claimed_observed_and_canonical_flags_without_trust_cannot_validate():
    """Origin/publication labels alone create source proof or an operating permit."""
    material = unit_material()
    material["bundle"] = material["bundle"].model_copy(update={"evidence_kind": "observed"})
    claimed = toy_bounds(
        material, evidence_kind="verified_canonical", publication_sha256=h("UNTRUSTED")
    )
    report = assess_judge(**material, statistics=claimed)
    assert report["quality_conditions_pass"] and report["status"] == "unvalidated"
    assert set(report["reasons"]) == {"observations_unverified", "canonical_statistics_unavailable"}
