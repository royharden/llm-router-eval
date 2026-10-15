"""Single final-answer grammar. Private labels never enter a routing request."""

import re
from collections import Counter
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, model_validator

from ...callledger.models import Digest, Identifier, WireModel
from ...providers.models import Status
from ..policies import well_formed_final

DECIMAL = re.compile(r"[+-]?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", re.ASCII)
INTEGER = re.compile(r"[+-]?(?:0|[1-9][0-9]*)", re.ASCII)


class ScoringLabel(WireModel):
    prompt_id: Identifier
    family_id: Identifier
    kind: Literal["gsm8k", "mmlu", "code", "exact", "rubric"]
    answer: Annotated[str, Field(min_length=1, max_length=8000)] | None
    rubric: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=2000)]], Field(max_length=8)
    ] = []
    version: Literal["anchored-v1"] = "anchored-v1"

    @model_validator(mode="after")
    def label_shape(self):
        if self.kind == "rubric":
            if not self.rubric or self.answer is not None:
                raise ValueError("rubric_label")
        elif self.answer is None or self.rubric:
            raise ValueError("answer_label")
        if self.kind == "gsm8k" and DECIMAL.fullmatch(self.answer) is None:
            raise ValueError("numeric_gold_grammar")
        if self.kind == "code" and INTEGER.fullmatch(self.answer) is None:
            raise ValueError("integer_gold_grammar")
        if self.kind == "mmlu" and self.answer not in {"A", "B", "C", "D"}:
            raise ValueError("option_gold_grammar")
        return self


class CandidateAnswer(WireModel):
    prompt_id: Identifier
    status: Status
    text: Annotated[str, Field(max_length=16000)] | None
    capture_sha256: Digest | None = None

    @model_validator(mode="after")
    def byte_bound(self):
        if self.text is not None and len(self.text.encode("utf-8")) > 16000:
            raise ValueError("candidate_bytes")
        return self


class ScoreRecord(WireModel):
    prompt_id: Identifier
    family_id: Identifier
    kind: Literal["gsm8k", "mmlu", "code", "exact", "rubric"]
    status: Literal["passed", "failed", "unscored"]
    passed: bool | None
    reason: Literal[
        "matched",
        "wrong_answer",
        "provider_failed",
        "missing_response",
        "final_format",
        "answer_format",
        "judge_unvalidated",
    ]
    version: Literal["anchored-v1"] = "anchored-v1"

    @model_validator(mode="after")
    def consistency(self):
        if self.passed != {"passed": True, "failed": False, "unscored": None}[self.status]:
            raise ValueError("score_status")
        if (self.status == "passed") != (self.reason == "matched") or (
            self.status == "unscored"
        ) != (self.reason == "judge_unvalidated"):
            raise ValueError("score_reason")
        return self


def score(label: ScoringLabel, candidate: CandidateAnswer) -> ScoreRecord:
    label = ScoringLabel.model_validate(label.model_dump(mode="json"))
    candidate = CandidateAnswer.model_validate(candidate.model_dump(mode="json"))
    if label.prompt_id != candidate.prompt_id:
        raise ValueError("score_item_binding")

    def record(status, reason):
        return ScoreRecord(
            prompt_id=label.prompt_id,
            family_id=label.family_id,
            kind=label.kind,
            status=status,
            passed={"passed": True, "failed": False, "unscored": None}[status],
            reason=reason,
        )

    if candidate.status != "ok":
        return record("failed", "provider_failed")
    if candidate.text is None or not candidate.text.strip():
        return record("failed", "missing_response")
    if label.kind == "rubric":
        return record("unscored", "judge_unvalidated")
    if not well_formed_final(candidate.text):
        return record("failed", "final_format")
    value = candidate.text.replace("\r\n", "\n").rstrip("\n").splitlines()[-1][len("FINAL: ") :]
    if label.kind in {"gsm8k", "code"}:
        grammar = DECIMAL if label.kind == "gsm8k" else INTEGER
        if grammar.fullmatch(value) is None:
            return record("failed", "answer_format")
        matched = Decimal(value) == Decimal(label.answer)
    elif label.kind == "mmlu":
        if value not in {"A", "B", "C", "D"}:
            return record("failed", "answer_format")
        matched = value == label.answer
    else:
        matched = value == label.answer
    return record("passed" if matched else "failed", "matched" if matched else "wrong_answer")


def summarize_scores(records: list[ScoreRecord], *, assigned_ids: list[str]) -> dict:
    checked = [ScoreRecord.model_validate(r.model_dump(mode="json")) for r in records]
    if len(set(assigned_ids)) != len(assigned_ids) or not assigned_ids:
        raise ValueError("assigned_ids")
    if len({r.prompt_id for r in checked}) != len(checked) or any(
        r.prompt_id not in assigned_ids for r in checked
    ):
        raise ValueError("score_assignment")
    counts = Counter(r.status for r in checked)
    missing = len(assigned_ids) - len(checked)
    return dict(
        assigned=len(assigned_ids),
        recorded=len(checked),
        scored=counts["passed"] + counts["failed"],
        passed=counts["passed"],
        failed=counts["failed"],
        unscored=counts["unscored"],
        missing=missing,
        passes_per_assigned=counts["passed"] / len(assigned_ids),
        completeness="complete" if missing == 0 and counts["unscored"] == 0 else "incomplete",
        by_reason=dict(Counter(r.reason for r in checked)),
    )
