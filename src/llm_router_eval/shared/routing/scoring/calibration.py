"""S3 cohort/count wrappers. Generic statistics must come from canonical S2 source.

No Wilson/bootstrap/kappa implementation lives here. With no actual trusted canonical
publication or observations, diagnostic numbers cannot validate an operating gate.
"""

from collections import Counter, defaultdict
from typing import Annotated, Literal

from pydantic import Field, model_validator

from ...callledger.models import Digest, Identifier, WireModel, canonical_digest
from ..policies import CheckerCalibration
from .labels import HumanAdmission, PairSpec, admit_human_labels

Probability = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Count = Annotated[int, Field(ge=0)]


class Prediction(WireModel):
    pair_id: Identifier
    answer_response_sha256: Digest
    status: Literal["ok", "failed"]
    probability: Probability | None = None
    passed: bool | None = None
    capture_sha256: Digest | None

    @model_validator(mode="after")
    def usable(self):
        if self.status == "failed" and (self.probability is not None or self.passed is not None):
            raise ValueError("failed_prediction_usable")
        return self


class ObservationBundle(WireModel):
    evidence_kind: Literal["synthetic_control", "observed"]
    split: Literal["dev", "heldout"]
    config_sha256: Digest
    producer_receipt_sha256: Digest
    predictions: list[Prediction]


class JudgeStatisticsRequest(WireModel):
    """Owned domain port, not a guess at the unpublished S2 function names/API."""

    config_sha256: Digest
    natural_prompt_matches: dict[Identifier, list[bool]]
    natural_confusion: dict[str, int]
    replicates: Literal[2000] = 2000
    seed: Literal[20261014] = 20261014
    confidence: Literal["two_sided_95"] = "two_sided_95"


class JudgeStatisticsEvidence(WireModel):
    evidence_kind: Literal["synthetic_control", "verified_canonical"]
    input_sha256: Digest
    publication_sha256: Digest | None
    replicates: Literal[2000]
    seed: Literal[20261014]
    confidence: Literal["two_sided_95"]
    wilson_lower: Probability
    wilson_upper: Probability
    prompt_cluster_lower: Probability
    prompt_cluster_upper: Probability
    kappa: Annotated[float, Field(ge=-1, le=1, allow_inf_nan=False)] | None

    @model_validator(mode="after")
    def ordered_bounds(self):
        if (
            self.wilson_lower > self.wilson_upper
            or self.prompt_cluster_lower > self.prompt_cluster_upper
        ):
            raise ValueError("interval_order")
        return self


def material(pairs, human_payload, admission, bundle, config_sha256, trusted_observation_receipts):
    checked = [PairSpec.model_validate(p.model_dump(mode="json")) for p in pairs]
    humans = admit_human_labels(human_payload, admission=admission, pairs=checked)
    bundle = ObservationBundle.model_validate(bundle.model_dump(mode="json"))
    if bundle.split != "dev":
        raise ValueError("calibration_dev_only")
    if bundle.config_sha256 != config_sha256:
        raise ValueError("observation_configuration")
    by_id = {p.pair_id: p for p in checked}
    predictions = {}
    for pred in bundle.predictions:
        if pred.pair_id not in by_id or pred.pair_id in predictions:
            raise ValueError("prediction_ids")
        if pred.answer_response_sha256 != by_id[pred.pair_id].response_sha256:
            raise ValueError("response_binding")
        predictions[pred.pair_id] = pred
    observed = (
        bundle.evidence_kind == "observed"
        and trusted_observation_receipts.get(bundle.producer_receipt_sha256)
        == canonical_digest(bundle.model_dump(mode="json"))
        and all(p.capture_sha256 is not None for p in predictions.values())
    )
    return checked, {label.pair_id: label.passed for label in humans.labels}, predictions, observed


class CheckerThreshold(WireModel):
    threshold: Literal[0.5, 0.7, 0.9]
    accepted: Count
    false_accepts: Count
    human_fail_cases: Count
    assigned: Count
    observed: Count
    false_accepts_per_accepted: Probability | None
    false_accepts_per_human_fail: Probability | None
    acceptance_coverage: Probability
    qualifies: bool


class CheckerReport(WireModel):
    status: Literal["validated", "unvalidated"]
    backend: Literal["jev", "llm"]
    config_sha256: Digest
    input_sha256: Digest
    assigned: Count
    observed: Count
    missing: Count
    human_fail_cases: Count
    cohort_counts: dict[Literal["cheap", "frontier", "corruption"], int]
    thresholds: list[CheckerThreshold]
    selected_threshold: Literal[0.5, 0.7, 0.9] | None
    reasons: list[
        Literal["observations_unverified", "incomplete_observations", "no_qualifying_threshold"]
    ]

    def as_policy_calibration(
        self, *, receipt_sha256: Digest, trusted_report_receipts: frozenset[str] = frozenset()
    ):
        checked = CheckerReport.model_validate(self.model_dump(mode="json"))
        if checked.status != "validated" or receipt_sha256 not in trusted_report_receipts:
            return None
        selected = next(
            (
                v
                for v in checked.thresholds
                if v.threshold == checked.selected_threshold and v.qualifies
            ),
            None,
        )
        if selected is None:
            return None
        return CheckerCalibration(
            status="validated",
            backend=checked.backend,
            threshold=selected.threshold,
            config_sha256=checked.config_sha256,
            receipt_sha256=receipt_sha256,
            dev_responses=checked.assigned,
            human_fail_cases=checked.human_fail_cases,
            accepted=selected.accepted,
            false_accepts=selected.false_accepts,
        )


def assess_checker(
    *,
    pairs: list[PairSpec],
    human_payload: bytes,
    admission: HumanAdmission,
    bundle: ObservationBundle,
    backend: Literal["jev", "llm"],
    config_sha256: Digest,
    trusted_observation_receipts: dict[str, str] | None = None,
) -> CheckerReport:
    checked, truth, predictions, source_verified = material(
        pairs, human_payload, admission, bundle, config_sha256, trusted_observation_receipts or {}
    )
    usable = {
        key: pred
        for key, pred in predictions.items()
        if pred.status == "ok" and pred.probability is not None
    }
    n = len(checked)
    human_fails = sum(not truth[p.pair_id] for p in checked)
    complete = len(usable) == n
    thresholds = []
    for threshold in [0.5, 0.7, 0.9]:
        accepted = [key for key, pred in usable.items() if pred.probability >= threshold]
        false = sum(not truth[key] for key in accepted)
        qualifies = (
            complete
            and n >= 40
            and human_fails >= 20
            and len(accepted) >= 20
            and false * 20 <= len(accepted)
        )
        thresholds.append(
            CheckerThreshold(
                threshold=threshold,
                accepted=len(accepted),
                false_accepts=false,
                human_fail_cases=human_fails,
                assigned=n,
                observed=len(usable),
                false_accepts_per_accepted=false / len(accepted) if accepted else None,
                false_accepts_per_human_fail=false / human_fails if human_fails else None,
                acceptance_coverage=len(accepted) / n,
                qualifies=qualifies,
            )
        )
    selected = next((t.threshold for t in thresholds if t.qualifies), None)
    reasons = []
    if not source_verified:
        reasons.append("observations_unverified")
    if not complete:
        reasons.append("incomplete_observations")
    if selected is None:
        reasons.append("no_qualifying_threshold")
    return CheckerReport(
        status="validated" if not reasons else "unvalidated",
        backend=backend,
        config_sha256=config_sha256,
        input_sha256=canonical_digest(
            dict(
                pairs=[p.model_dump(mode="json") for p in checked],
                human_file_sha256=admission.label_file_sha256,
                bundle=bundle.model_dump(mode="json"),
            )
        ),
        assigned=n,
        observed=len(usable),
        missing=n - len(usable),
        human_fail_cases=human_fails,
        cohort_counts=dict(Counter(p.candidate_source for p in checked)),
        thresholds=thresholds,
        selected_threshold=selected,
        reasons=reasons,
    )


def judge_counts(
    *,
    pairs: list[PairSpec],
    human_payload: bytes,
    admission: HumanAdmission,
    bundle: ObservationBundle,
    config_sha256: Digest,
    trusted_observation_receipts: dict[str, str] | None = None,
):
    checked, truth, predictions, source_verified = material(
        pairs, human_payload, admission, bundle, config_sha256, trusted_observation_receipts or {}
    )
    catalog = defaultdict(list)
    for pair in checked:
        catalog[pair.prompt_id].append(pair.candidate_source)
    if (
        len(checked) != 60
        or len(catalog) != 20
        or any(sorted(v) != ["cheap", "corruption", "frontier"] for v in catalog.values())
    ):
        raise ValueError("judge_pair_catalog")
    cohorts = {}
    groups = defaultdict(list)
    for name in ["natural", "corrupted"]:
        rows = [p for p in checked if (p.candidate_source == "corruption") == (name == "corrupted")]
        confusion = Counter({"true_pass": 0, "true_fail": 0, "false_pass": 0, "false_fail": 0})
        for pair in sorted(rows, key=lambda p: p.pair_id):
            pred = predictions.get(pair.pair_id)
            if pred is None or pred.status != "ok" or pred.passed is None:
                continue
            correct = pred.passed == truth[pair.pair_id]
            if name == "natural":
                groups[pair.prompt_id].append(correct)
            code = (
                ("true_pass" if pred.passed else "false_fail")
                if truth[pair.pair_id]
                else ("false_pass" if pred.passed else "true_fail")
            )
            confusion[code] += 1
        observed = sum(confusion.values())
        matches = confusion["true_pass"] + confusion["true_fail"]
        human_fails = sum(not truth[p.pair_id] for p in rows)
        cohorts[name] = dict(
            assigned=len(rows),
            observed=observed,
            missing=len(rows) - observed,
            human_fail_cases=human_fails,
            confusion=dict(confusion),
            agreement=matches / observed if observed else None,
            agreement_per_assigned=matches / len(rows),
            false_pass_per_human_fail=confusion["false_pass"] / human_fails
            if human_fails
            else None,
        )
    request = None
    if cohorts["natural"]["missing"] == 0:
        request = JudgeStatisticsRequest(
            config_sha256=config_sha256,
            natural_prompt_matches=dict(sorted(groups.items())),
            natural_confusion=cohorts["natural"]["confusion"],
        )
    return cohorts, request, source_verified


def assess_judge(
    *,
    pairs: list[PairSpec],
    human_payload: bytes,
    admission: HumanAdmission,
    bundle: ObservationBundle,
    config_sha256: Digest,
    statistics: JudgeStatisticsEvidence | None = None,
    trusted_observation_receipts: dict[str, str] | None = None,
    trusted_statistics_publications: frozenset[str] = frozenset(),
) -> dict:
    cohorts, request, source_verified = judge_counts(
        pairs=pairs,
        human_payload=human_payload,
        admission=admission,
        bundle=bundle,
        config_sha256=config_sha256,
        trusted_observation_receipts=trusted_observation_receipts,
    )
    reasons = []
    lower = None
    stats_verified = False
    stats = None
    if not source_verified:
        reasons.append("observations_unverified")
    if any(v["missing"] for v in cohorts.values()):
        reasons.append("incomplete_observations")
    if statistics is not None:
        stats = JudgeStatisticsEvidence.model_validate(statistics.model_dump(mode="json"))
        if request is None or stats.input_sha256 != canonical_digest(
            request.model_dump(mode="json")
        ):
            raise ValueError("statistics_input_binding")
        lower = min(stats.wilson_lower, stats.prompt_cluster_lower)
        stats_verified = (
            stats.evidence_kind == "verified_canonical"
            and stats.publication_sha256 in trusted_statistics_publications
        )
    if not stats_verified:
        reasons.append("canonical_statistics_unavailable")
    natural = cohorts["natural"]
    conditions = (
        natural["agreement"] is not None
        and natural["agreement"] >= 0.9
        and lower is not None
        and lower >= 0.8
        and natural["human_fail_cases"] >= 20
        and natural["false_pass_per_human_fail"] is not None
        and natural["false_pass_per_human_fail"] <= 0.05
    )
    if not conditions:
        reasons.append("natural_quality_gate_not_met")
    return dict(
        status="validated" if not reasons else "unvalidated",
        reasons=reasons,
        cohorts=cohorts,
        conservative_operating_lower=lower,
        statistics=None if stats is None else stats.model_dump(mode="json"),
        statistics_request=None if request is None else request.model_dump(mode="json"),
        quality_conditions_pass=conditions,
        headline_rubric_permitted=not reasons,
        limitations=(
            "No generic statistics executed by this domain wrapper. "
            "Trust requires actual human/observation source and canonical statistics publication. "
            "Finite prompt-cluster intervals do not guarantee nominal coverage; "
            "synthetic diagnostic bounds cannot grant actual acceptance."
        ),
    )
