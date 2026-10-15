"""S3 natural-cohort adapter onto the unchanged, published S2 statistics implementation.

These calculations do not acquire, authenticate or author human labels or model observations.
"""

from typing import Literal

from ...callledger.models import Digest, WireModel, canonical_digest
from ...evalkit.models import Outcome, ResamplingSpec
from ...evalkit.resampling import paired_group_resample
from ...evalkit.statistics import accuracy, agreement, wilson_interval
from .calibration import JudgeStatisticsEvidence, judge_counts, material

CANONICAL_PUBLICATION_SHA256 = "28dbf310102f0bf316525c4945d474b9bca8d7276a9753dd72cfbb824c4d2af3"
CANONICAL_SOURCE_COMMIT = "249262839c4247ff37cc5a75c6a89a422738c759"


class CanonicalJudgeExecution(WireModel):
    status: Literal["computed", "unavailable"]
    reason: Literal["natural_observations_incomplete", "interval_unavailable"] | None
    publication_sha256: Digest
    paired_inputs_sha256: Digest | None
    statistics: JudgeStatisticsEvidence | None
    group_count: int
    valid_draws: int
    draws_sha256: Digest | None
    rng_version: str | None
    python_version: str | None
    support_policy: str | None
    kappa_null_reason: str | None


def canonical_judge_statistics(
    *, pairs, human_payload, admission, bundle, config_sha256, trusted_observation_receipts=None
) -> CanonicalJudgeExecution:
    """Admit actual paired inputs through existing domain checks, then call canonical math.

    Synthetic/unverified observations stay synthetic even though canonical code actually ran.
    The caller still needs independently admitted human and model evidence for an operating gate.
    """
    trusted = trusted_observation_receipts or {}
    cohorts, request, source_verified = judge_counts(
        pairs=pairs,
        human_payload=human_payload,
        admission=admission,
        bundle=bundle,
        config_sha256=config_sha256,
        trusted_observation_receipts=trusted,
    )
    common = dict(
        publication_sha256=CANONICAL_PUBLICATION_SHA256,
        paired_inputs_sha256=None,
        statistics=None,
        group_count=0,
        valid_draws=0,
        draws_sha256=None,
        rng_version=None,
        python_version=None,
        support_policy=None,
        kappa_null_reason=None,
    )
    if request is None:
        return CanonicalJudgeExecution(
            status="unavailable", reason="natural_observations_incomplete", **common
        )
    checked, truth, predictions, _ = material(
        pairs, human_payload, admission, bundle, config_sha256, trusted
    )
    natural = sorted(
        (p for p in checked if p.candidate_source != "corruption"), key=lambda p: p.pair_id
    )
    # These are actual paired labels, not reconstructed cases from aggregate confusion counts.
    paired = tuple(
        (p.pair_id, p.prompt_id, truth[p.pair_id], predictions[p.pair_id].passed) for p in natural
    )
    outcomes = tuple(
        Outcome.model_validate(
            dict(
                item_id=pair_id,
                group_id=prompt_id,
                dataset="judge-natural",
                split="dev",
                method="judge",
                field=None,
                **{"class": None},
                eligibility="eligible",
                status="ok",
                correct=human == machine,
                predicted=machine,
                expected=human,
                human_touched_prediction=False,
                # Statistics consume retained observations; an observed bundle is not live dispatch.
                evidence_origin="recorded_replay" if source_verified else "synthetic",
                issues=(),
                ledger_refs=(),
            )
        )
        for pair_id, prompt_id, human, machine in paired
    )
    spec = ResamplingSpec(
        version="s3-judge-natural-v1",
        unit="group_id",
        seed=request.seed,
        draws=request.replicates,
        level=0.95,
        method="percentile",
        group_order=tuple(sorted(request.natural_prompt_matches)),
        stratum_map=None,
        min_support=40,
        min_valid_draws=2000,
        statistic_version="s3-natural-paired-label-agreement-v1",
        rng_version="Python3.12/random",
    )
    resampled = paired_group_resample({"judge": outcomes}, spec, accuracy)
    estimate = resampled.methods["judge"]
    confusion = cohorts["natural"]["confusion"]
    wilson = wilson_interval(confusion["true_pass"] + confusion["true_fail"], len(outcomes))
    paired_agreement = agreement(tuple(row[2] for row in paired), tuple(row[3] for row in paired))
    common.update(
        paired_inputs_sha256=canonical_digest(paired),
        group_count=resampled.group_count,
        valid_draws=estimate.valid_draws,
        draws_sha256=resampled.draws_sha256,
        rng_version=resampled.rng_version,
        python_version=resampled.python_version,
        support_policy=resampled.support_policy,
        kappa_null_reason=paired_agreement["kappa"].null_reason,
    )
    if wilson is None or estimate.interval is None:
        return CanonicalJudgeExecution(
            status="unavailable", reason="interval_unavailable", **common
        )
    common["statistics"] = JudgeStatisticsEvidence(
        evidence_kind="verified_canonical" if source_verified else "synthetic_control",
        input_sha256=canonical_digest(request.model_dump(mode="json")),
        publication_sha256=CANONICAL_PUBLICATION_SHA256,
        replicates=request.replicates,
        seed=request.seed,
        confidence=request.confidence,
        wilson_lower=wilson[0],
        wilson_upper=wilson[1],
        prompt_cluster_lower=estimate.interval.low,
        prompt_cluster_upper=estimate.interval.high,
        kappa=paired_agreement["kappa"].value,
    )
    return CanonicalJudgeExecution(status="computed", reason=None, **common)
