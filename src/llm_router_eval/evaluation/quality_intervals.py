"""Domain population selection for the unchanged canonical paired family bootstrap."""

from ..shared.evalkit.models import ResamplingSpec
from ..shared.evalkit.resampling import paired_group_resample
from ..shared.evalkit.statistics import accuracy
from .domain import AnswerAdapter


def quality_intervals(result, view, reference_outcomes):
    gold = tuple(
        g
        for g in view.split_records(result.comparison.split)
        if AnswerAdapter.label(g).kind != "rubric"
    )
    public = dict(
        status="computed" if gold else "empty",
        case_N=len(gold),
        family_N=len({g.group_id for g in gold}),
        unit="family",
        seed=20261014,
        draws=2000,
        level=0.95,
        methods={},
        interpretation="Authored-control sampling illustration, not model-quality inference.",
        plan_drift="Canonical S2 v1 uses2000 draws rather than proposed1000; no new engine.",
    )
    if not gold:
        return public
    ids = {g.item_id for g in gold}
    measured = {
        name: tuple(o for o in method.outcomes if o.item_id in ids)
        for name, method in result.comparison.methods.items()
        if method.status == "executed"
    }
    measured.update(reference_outcomes)
    spec = ResamplingSpec(
        version="paired-family-v1",
        unit="group_id",
        seed=20261014,
        draws=2000,
        level=0.95,
        method="percentile",
        group_order=tuple(sorted({g.group_id for g in gold})),
        stratum_map=None,
        min_support=1,
        min_valid_draws=2000,
        statistic_version=AnswerAdapter.metric_specs()[0].statistic_version,
        rng_version="Python3.12/random",
    )
    sample = paired_group_resample(measured, spec, accuracy)
    public.update(
        draws_sha256=sample.draws_sha256,
        rng_version=sample.rng_version,
        python_version=sample.python_version,
        support_policy=sample.support_policy,
    )
    for name, estimate in sample.methods.items():
        public["methods"][name] = dict(
            interval=None
            if estimate.interval is None
            else estimate.interval.model_dump(mode="json"),
            valid_draws=estimate.valid_draws,
            null_reason=estimate.null_reason,
        )
    return public
