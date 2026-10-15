"""Reporting-only random/oracle references; never imported by executable policies."""

import hashlib
import json
import math
from decimal import Decimal
from typing import Literal

from pydantic import model_validator

from ...callledger.models import Money, WireModel
from ..code_tier import can_handle
from ..context import RouteInput, Tier


def random_reference(item: RouteInput, *, seed: int, weights: dict[str, float]) -> Tier:
    item = RouteInput.model_validate(item.model_dump(mode="json"))
    if type(seed) is not int or seed < 0 or set(weights) - {"code", "cheap", "frontier"}:
        raise ValueError("random_config")
    if any(
        isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0
        for v in weights.values()
    ):
        raise ValueError("random_weights")
    eligible = [
        tier
        for tier in ["code", "cheap", "frontier"]
        if (tier != "code" or can_handle(item.text)) and weights.get(tier, 0) > 0
    ]
    total = sum(weights[tier] for tier in eligible)
    if not total or not math.isfinite(total):
        raise ValueError("no_eligible_weight")
    raw = json.dumps(
        [seed, item.prompt_id, item.text], ensure_ascii=False, separators=(",", ":")
    ).encode()
    draw = int.from_bytes(hashlib.sha256(raw).digest()[:8], "big") / 2**64
    cumulative = 0.0
    for tier in eligible:
        cumulative += weights[tier] / total
        if draw < cumulative:
            return tier
    return eligible[-1]


class CapturedCandidate(WireModel):
    tier: Tier
    passed: bool
    cost_usd: Money | None


class OracleDecision(WireModel):
    tier: Tier | None
    passed: bool
    status: Literal["complete", "unknown_cost"]

    @model_validator(mode="after")
    def binding(self):
        if (self.status == "complete") != (self.tier is not None):
            raise ValueError("oracle_binding")
        return self


def cheapest_passing_oracle(
    item: RouteInput, candidates: list[CapturedCandidate]
) -> OracleDecision:
    item = RouteInput.model_validate(item.model_dump(mode="json"))
    checked = [CapturedCandidate.model_validate(c.model_dump(mode="json")) for c in candidates]
    if not checked or len({c.tier for c in checked}) != len(checked):
        raise ValueError("oracle_candidates")
    checked = [c for c in checked if c.tier != "code" or can_handle(item.text)]
    if not checked:
        raise ValueError("no_eligible_candidates")
    passing = [c for c in checked if c.passed]
    pool = passing or checked
    if any(c.cost_usd is None for c in pool):
        return OracleDecision(tier=None, passed=bool(passing), status="unknown_cost")
    chosen = min(pool, key=lambda c: (Decimal(c.cost_usd), c.tier))
    return OracleDecision(tier=chosen.tier, passed=bool(passing), status="complete")
