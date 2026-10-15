"""Explicit per-policy public context; inference/accounting stays with the caller."""

from collections.abc import Callable
from typing import Annotated, Literal

from pydantic import Field, model_validator

from ...callledger.models import Digest, Identifier, WireModel
from ...providers.models import GenerateRequest, Result, TypedJudgmentRequest

Tier = Literal["code", "cheap", "frontier"]


class RouteInput(WireModel):
    prompt_id: Identifier
    text: Annotated[str, Field(min_length=1, max_length=8000)]

    @model_validator(mode="after")
    def prompt_bound(self):
        if not self.text.strip() or len(self.text.encode("utf-8")) > 8000:
            raise ValueError("route_input_bytes")
        return self

    @classmethod
    def from_data_view(cls, value: dict):
        if set(value) != {"task_id", "prompt"}:
            raise ValueError("public_view_keys")
        return cls(prompt_id=value["task_id"], text=value["prompt"])


class RouteDecision(WireModel):
    tier: Tier
    reasons: list[Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]]
    confidence: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)] | None
    consumed_span_ids: list[Identifier]
    version: Literal["routing-v1"] = "routing-v1"

    @model_validator(mode="after")
    def unique_spans(self):
        if len(set(self.consumed_span_ids)) != len(self.consumed_span_ids):
            raise ValueError("duplicate_consumption")
        return self


class TierAnswer(WireModel):
    status: Literal["ok", "failed"]
    text: Annotated[str, Field(max_length=16000)] | None
    span_ids: Annotated[list[Identifier], Field(min_length=1)]

    @model_validator(mode="after")
    def valid_answer(self):
        if self.status == "ok" and (self.text is None or not self.text.strip()):
            raise ValueError("empty_answer")
        if self.text is not None and len(self.text.encode("utf-8")) > 16000:
            raise ValueError("answer_bytes")
        if len(set(self.span_ids)) != len(self.span_ids):
            raise ValueError("duplicate_spans")
        return self

    @classmethod
    def from_result(cls, result: Result, *, span_ids: list[str]):
        checked = Result.model_validate(result.model_dump(mode="json"))
        usable = (
            checked.status == "ok"
            and checked.kind == "generated"
            and checked.parsed is None
            and checked.text is not None
            and bool(checked.text.strip())
        )
        return cls(status="ok" if usable else "failed", text=checked.text, span_ids=span_ids)


class ObservedResult(WireModel):
    result: Result
    span_ids: Annotated[list[Identifier], Field(min_length=1)]


class RouteContext:
    """One item/policy; callbacks must use their actual admitted Provider/Ledger controller.

    No network client, secret lookup, score labels, oracle or implicit budget is supplied here.
    The runner owns final-tier execution and durable span/accounting provenance.
    """

    def __init__(
        self,
        *,
        item: RouteInput,
        cheap_model: str,
        jev_model: str,
        answer: Callable[[Tier], TierAnswer],
        generate: Callable[[str, GenerateRequest], ObservedResult],
        judge: Callable[[str, TypedJudgmentRequest], ObservedResult],
        config_sha256: Digest,
        trusted_calibration_receipts: frozenset[str] = frozenset(),
    ):
        self.item = RouteInput.model_validate(item.model_dump(mode="json"))
        if not cheap_model or not jev_model:
            raise ValueError("explicit_models_required")
        self.cheap_model, self.jev_model = cheap_model, jev_model
        self.config_sha256 = config_sha256
        self.trusted_calibration_receipts = frozenset(trusted_calibration_receipts)
        self._answer, self._generate, self._judge = answer, generate, judge
        self._consumed: list[str] = []
        self._answers: dict[str, TierAnswer] = {}
        self._started = False

    def begin(self, item: RouteInput):
        checked = RouteInput.model_validate(item.model_dump(mode="json"))
        if self._started or checked != self.item:
            raise ValueError("fresh_context_binding")
        self._started = True

    @property
    def consumed_span_ids(self) -> list[str]:
        return list(self._consumed)

    def _record(self, spans):
        if not self._started or len(set(spans)) != len(spans) or set(spans) & set(self._consumed):
            raise ValueError("span_consumption_binding")
        self._consumed.extend(spans)

    def answer(self, tier: Tier) -> TierAnswer:
        if tier not in {"code", "cheap", "frontier"}:
            raise ValueError("unknown_tier")
        if tier not in self._answers:
            value = self._answer(tier)
            checked = TierAnswer.model_validate(value.model_dump(mode="json"))
            self._record(checked.span_ids)
            self._answers[tier] = checked
        return TierAnswer.model_validate(self._answers[tier].model_dump(mode="json"))

    def generate(self, purpose: str, request: GenerateRequest) -> Result:
        observed = self._generate(purpose, request)
        checked = ObservedResult.model_validate(observed.model_dump(mode="json"))
        self._record(checked.span_ids)
        return checked.result

    def judge(self, purpose: str, request: TypedJudgmentRequest) -> Result:
        observed = self._judge(purpose, request)
        checked = ObservedResult.model_validate(observed.model_dump(mode="json"))
        self._record(checked.span_ids)
        return checked.result
