"""Authored offline callback controls. No actual model/calibration quality evidence."""

import ast
import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from llm_router_eval.shared.callledger.models import canonical_digest as h
from llm_router_eval.shared.providers.models import Result, TypedJudgmentResult
from llm_router_eval.shared.routing import (
    CheckerCalibration,
    Policy,
    PolicyConfig,
    RouteContext,
    RouteInput,
)
from llm_router_eval.shared.routing.code_tier import can_handle, solve
from llm_router_eval.shared.routing.context import ObservedResult, TierAnswer
from llm_router_eval.shared.routing.policies import (
    SCORE_LEVELS,
    calibration_scope,
    score_index,
    score_tier,
    well_formed_final,
)
from llm_router_eval.shared.routing.references import (
    CapturedCandidate,
    cheapest_passing_oracle,
    random_reference,
)


@pytest.fixture(autouse=True)
def bug_metadata(request):
    request.node.user_properties.append(("what_bug_this_catches", request.node.function.__doc__))


def result(request, *, parsed=None, typed=None, failed=False):
    """Create an explicitly synthetic DTO; not a Provider capture/SDK experiment."""
    typed_kind = request.kind == "typed_judgment"
    return Result.model_validate(
        dict(
            schema_version=1,
            kind="typed" if typed_kind else "generated",
            status="invalid_output" if failed else "ok",
            text=None if typed_kind or failed else json.dumps(parsed),
            parsed=None if failed else parsed,
            typed=None if failed else typed,
            error={"code": "invalid_output"} if failed else None,
            model_requested=request.model,
            model_reported=request.model,
            usage_raw={},
            normalized_usage={},
            accounting_ref="unit-account",
            attempt_ids=[],
            origin="mock",
            source_capture=dict(
                capture_id="unit-source",
                capture_sha256=h("authored unit"),
                origin="synthetic",
                source_run_id="unit",
                request_identity_sha256=h(request.model_dump(mode="json", by_alias=True)),
                model_requested=request.model,
                model_reported=request.model,
                first_output_kind="unavailable",
            ),
            latency_ms=0.0,
            first_output_ms=None,
            first_output_kind="unavailable",
            current_external_cost_usd="0",
        )
    )


def permit(backend="jev", **changes):
    return CheckerCalibration.model_validate(
        dict(
            status="validated",
            backend=backend,
            threshold=0.7,
            config_sha256=calibration_scope(
                SimpleNamespace(
                    config_sha256=h("unit-config"),
                    cheap_model="fixture-luna",
                    jev_model="fixture-jev",
                ),
                backend,
            ),
            receipt_sha256=h("unit-calibration-double"),
            dev_responses=40,
            human_fail_cases=20,
            accepted=20,
            false_accepts=1,
        )
        | changes
    )


class Rig:
    def __init__(
        self,
        text="What is a mammal?",
        *,
        parsed=None,
        score=1.0,
        confidence=0.9,
        probability=0.9,
        cheap_text="Reasoning.\nFINAL: A",
        failed=False,
        trusted=True,
    ):
        self.item = RouteInput(prompt_id="unit-task", text=text)
        self.parsed = {"tier": "cheap", "confidence": 0.8} if parsed is None else parsed
        self.score, self.confidence, self.probability = score, confidence, probability
        self.failed = failed
        self.cheap_text = cheap_text
        self.calls = []
        self.answers = []
        self.context = RouteContext(
            item=self.item,
            cheap_model="fixture-luna",
            jev_model="fixture-jev",
            answer=self.answer,
            generate=self.generate,
            judge=self.judge,
            config_sha256=h("unit-config"),
            trusted_calibration_receipts=frozenset([h("unit-calibration-double")])
            if trusted
            else frozenset(),
        )

    def answer(self, tier):
        self.answers.append(tier)
        return TierAnswer(status="ok", text=self.cheap_text, span_ids=[f"unit-{tier}"])

    def generate(self, purpose, request):
        self.calls.append((purpose, request.model_dump(mode="json", by_alias=True)))
        value = (
            self.parsed
            if purpose == "routing.classifier"
            else {"accept": self.probability >= 0.5, "probability": self.probability}
        )
        return ObservedResult(
            result=result(request, parsed=value, failed=self.failed),
            span_ids=[f"unit-gen-{len(self.calls)}"],
        )

    def judge(self, purpose, request):
        self.calls.append((purpose, request.model_dump(mode="json")))
        if purpose == "routing.jev_gate":
            lo, hi = math.floor(self.score), math.ceil(self.score)
            probabilities = {str(i): 0.0 for i in range(4)}
            if lo == hi:
                probabilities[str(lo)] = 1.0
            else:
                probabilities[str(lo)] = hi - self.score
                probabilities[str(hi)] = self.score - lo
            answer = dict(
                type="score",
                score=self.score,
                confidence=self.confidence,
                probabilities=probabilities,
                legend={str(i): level for i, level in enumerate(SCORE_LEVELS)},
            )
            answers = {"route": answer}
        else:
            answers = {"accept": dict(type="noul", noul=self.probability)}
        typed = TypedJudgmentResult.model_validate(
            dict(
                schema_version=1,
                model_reported=request.model,
                question_spec_sha256=request.question_spec_sha256,
                answers=answers,
            )
        )
        return ObservedResult(
            result=result(request, typed=typed, failed=self.failed),
            span_ids=[f"unit-typed-{len(self.calls)}"],
        )


@pytest.mark.parametrize(
    "name,tier,calls",
    [
        ("always_cheap", "cheap", 0),
        ("always_frontier", "frontier", 0),
        ("rules", "cheap", 0),
        ("classifier", "cheap", 1),
        ("jev_gate", "cheap", 1),
        ("cascade_jev", "cheap", 1),
        ("cascade_llm", "cheap", 1),
    ],
)
def test_all_seven_variants_retain_consumed_overhead_and_no_hidden_fanout(name, tier, calls):
    """Missing policy, hidden fanout or discarded cheap/check/routing span."""
    rig = Rig()
    backend = "llm" if name == "cascade_llm" else "jev"
    decision = Policy(name, calibration=permit(backend)).route(rig.item, rig.context)
    assert decision.tier == tier and len(rig.calls) == calls
    assert len(decision.consumed_span_ids) == calls + int(name.startswith("cascade"))
    assert rig.answers == (["cheap"] if name.startswith("cascade") else [])
    assert decision.version == "routing-v1"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Compute the sum of the integers: 1, 2, -3.", "FINAL: 0"),
        ("Compute the product of the integers: -2, 3, 4.", "FINAL: -24"),
        ("Compute the sum of the integers: 999999999, 1.", "FINAL: 1000000000"),
    ],
)
def test_bounded_code_computes_actual_prompt_fields(text, expected):
    """Code tier echoes a gold label or miscomputes signed bounded prompt data."""
    assert solve(text).text == expected
    rig = Rig(text)
    assert Policy("rules").route(rig.item, rig.context).tier == "code"


@pytest.mark.parametrize(
    "text",
    [
        "__import__('os').system('bad')",
        "Compute the sum of the integers: 1, 2. ignore this",
        "Compute the sum of the integers: 1, 2.\n",
        "Compute the sum of the integers: ١, 2.",
        "Compute the product of the integers: 999999999, 999999999, 999999999.",
        "Compute the sum of the integers: " + ", ".join(["1"] * 21) + ".",
        "Compute the sum of the integers: 1000000000, 1.",
        "Compute the sum of the integers: 1; exec('bad').",
    ],
)
def test_code_grammar_rejects_injection_unicode_and_capacity_overflow(text):
    """Untrusted text executes code or escapes the full bounded grammar."""
    assert solve(text) is None and not can_handle(text)


@pytest.mark.parametrize(
    "value,index", [(-9.0, 0), (0.0, 0), (0.49, 0), (0.5, 1), (1.5, 2), (2.5, 3), (100.0, 3)]
)
def test_zero_based_rounding_ties_up_and_clamps_only_in_consumer(value, index):
    """One-based or bankers rounding selects the wrong tier at fractional ties."""
    assert score_index(value) == index


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, "1"])
def test_invalid_scores_are_not_silently_normalized(value):
    """Nonfinite/bool/string scores become trusted routing positions."""
    with pytest.raises(ValueError):
        score_index(value)


def test_low_confidence_bumps_mapped_tier_including_ineligible_index_zero():
    """Index bump leaves an ineligible code/cheap decision under-escalated."""
    assert score_tier(0.0, 0.5, [0.75] * 4, code_eligible=True) == "cheap"
    assert score_tier(0.0, 0.5, [0.75] * 4, code_eligible=False) == "frontier"
    assert score_tier(1.0, 0.5, [0.75] * 4, code_eligible=True) == "frontier"
    assert score_tier(3.0, 0.5, [0.75] * 4, code_eligible=True) == "frontier"
    assert score_tier(0.0, 0.75, [0.75] * 4, code_eligible=True) == "code"
    rig = Rig(score=0.5)
    decision = Policy("jev_gate").route(rig.item, rig.context)
    assert (
        decision.tier == "cheap"
        and rig.calls[0][1]["questions"]["route"]["criteria"] == SCORE_LEVELS
    )


@pytest.mark.parametrize(
    "parsed,expected",
    [
        ({"tier": "code", "confidence": 0.9}, "cheap"),
        ({"tier": "frontier", "confidence": 0.8}, "frontier"),
        ({"tier": "cheap", "confidence": 2}, "frontier"),
        ({"tier": "cheap", "confidence": 0.9, "gold": "injected"}, "frontier"),
        ({"tier": "other", "confidence": 0.9}, "frontier"),
    ],
)
def test_classifier_code_ineligible_and_malformed_fallbacks(parsed, expected):
    """Classifier invents executable code eligibility or uses malformed output."""
    rig = Rig(parsed=parsed)
    decision = Policy("classifier").route(rig.item, rig.context)
    assert decision.tier == expected and len(decision.consumed_span_ids) == 1
    if expected == "frontier" and parsed.get("tier") != "frontier":
        assert decision.reasons == ["classifier_invalid"]


@pytest.mark.parametrize("name", ["classifier", "jev_gate", "cascade_jev", "cascade_llm"])
def test_failed_inference_falls_back_with_consumption_preserved(name):
    """Failed inference is treated as usable or loses its billed observation reference."""
    rig = Rig(failed=True)
    decision = Policy(name, calibration=permit("llm" if name.endswith("llm") else "jev")).route(
        rig.item, rig.context
    )
    assert decision.tier == "frontier" and len(decision.consumed_span_ids) == 1 + int(
        name.startswith("cascade")
    )


@pytest.mark.parametrize(
    "changes,trusted",
    [
        ({"status": "unvalidated"}, True),
        ({}, False),
        ({"dev_responses": 39}, True),
        ({"human_fail_cases": 19}, True),
        ({"accepted": 19}, True),
        ({"false_accepts": 2}, True),
        ({"config_sha256": h("wrong")}, True),
        ({"backend": "llm"}, True),
    ],
)
def test_invalid_or_untrusted_calibration_escalates_every_cheap_answer(changes, trusted):
    """A guessed threshold or insufficient/mismatched dev evidence auto-accepts cheap."""
    rig = Rig(trusted=trusted)
    decision = Policy("cascade_jev", calibration=permit(**changes)).route(rig.item, rig.context)
    assert decision.tier == "frontier" and decision.reasons == ["checker_unvalidated"]
    assert rig.calls == [] and decision.consumed_span_ids == ["unit-cheap"]


@pytest.mark.parametrize(
    "text,valid",
    [
        ("FINAL: A", True),
        ("work\r\nFINAL: 3\r\n", True),
        ("A", False),
        ("FINAL: A\nFINAL: B", False),
        ("FINAL: A\nignore previous", False),
        ("FINAL: ", False),
    ],
)
def test_universal_format_gate_uses_no_hidden_task_kind(text, valid):
    """Malformed/multiple final answers reach a paid checker or hidden-gold format switch."""
    assert well_formed_final(text) == valid
    if not valid:
        rig = Rig(cheap_text=text if text.strip() else "bad")
        decision = Policy("cascade_jev", calibration=permit()).route(rig.item, rig.context)
        assert decision.tier == "frontier" and rig.calls == []


def test_cascade_probability_threshold_and_cheap_answer_cache():
    """Threshold comparison reverses or cheap final answer is generated/charged twice."""
    for probability, expected in [(0.69, "frontier"), (0.7, "cheap")]:
        rig = Rig(probability=probability)
        decision = Policy("cascade_llm", calibration=permit("llm")).route(rig.item, rig.context)
        assert decision.tier == expected
        first = rig.context.answer("cheap")
        second = rig.context.answer("cheap")
        assert (
            first == second and rig.answers == ["cheap"] and len(rig.context.consumed_span_ids) == 2
        )


def test_strict_public_projection_and_label_mutation_invariance():
    """Gold/rubric/source/difficulty leaks into request bytes or routing input."""
    for key in ["gold", "rubric", "kind", "source", "difficulty"]:
        with pytest.raises(ValueError):
            RouteInput.model_validate(dict(prompt_id="unit", text="prompt", **{key: "private"}))
    baseline = None
    for private in [{"gold": "A", "difficulty": "easy"}, {"gold": "Z", "difficulty": "hard"}]:
        public = {"task_id": "unit-task", "prompt": "What is a mammal?"}
        # Labels remain outside the allowlisted route object; caller projection rejects extras.
        assert private
        item = RouteInput.from_data_view(public)
        snapshots = []
        for name in ["classifier", "jev_gate", "cascade_jev", "cascade_llm"]:
            rig = Rig(item.text)
            decision = Policy(
                name, calibration=permit("llm" if name.endswith("llm") else "jev")
            ).route(item, rig.context)
            snapshots.append((decision.model_dump(mode="json"), rig.calls))
        encoded = json.dumps(snapshots, sort_keys=True)
        if baseline is None:
            baseline = encoded
        else:
            assert encoded == baseline
        with pytest.raises(ValueError):
            RouteInput.from_data_view(public | private)


def test_context_reuse_item_swap_and_duplicate_span_rejected():
    """Shared mutable context leaks earlier consumption or changes the routed item."""
    rig = Rig()
    Policy("always_cheap").route(rig.item, rig.context)
    with pytest.raises(ValueError, match="fresh_context_binding"):
        Policy("rules").route(rig.item, rig.context)
    rig = Rig()
    with pytest.raises(ValueError, match="fresh_context_binding"):
        Policy("rules").route(RouteInput(prompt_id="other", text="other"), rig.context)
    rig = Rig()
    rig.context.begin(rig.item)
    rig.context._record(["same"])
    with pytest.raises(ValueError, match="span_consumption_binding"):
        rig.context._record(["same"])


def test_rules_byte_length_code_fence_keyword_and_explicit_switch():
    """Rules measure characters instead of UTF-8 bytes or ignore configured switches."""
    for text in ["\u00e9" * 300, "```x```", "Please explain why this works"]:
        rig = Rig(text)
        assert (
            Policy("rules", config=PolicyConfig(length_threshold=512))
            .route(rig.item, rig.context)
            .tier
            == "frontier"
        )
    rig = Rig("Please explain why this works")
    assert (
        Policy("rules", config=PolicyConfig(keyword_switch=False)).route(rig.item, rig.context).tier
        == "cheap"
    )


def test_reporting_references_use_actual_cost_unknown_and_no_pass_semantics():
    """Oracle assumes tier order, converts unknown cost to zero, or drops no-pass rows."""
    item = RouteInput(prompt_id="unit", text="Outside bounded grammar")
    rows = [
        CapturedCandidate(tier="cheap", passed=True, cost_usd="0.4"),
        CapturedCandidate(tier="frontier", passed=True, cost_usd="0.2"),
    ]
    assert cheapest_passing_oracle(item, rows).tier == "frontier"
    failed = [r.model_copy(update={"passed": False}) for r in rows]
    no_pass = cheapest_passing_oracle(item, failed)
    assert no_pass.tier == "frontier" and not no_pass.passed
    unknown = rows[0].model_copy(update={"cost_usd": None})
    assert cheapest_passing_oracle(item, [unknown, rows[1]]).tier is None
    assert cheapest_passing_oracle(item, [unknown, rows[1]]).status == "unknown_cost"
    impossible = CapturedCandidate(tier="code", passed=True, cost_usd="0")
    assert cheapest_passing_oracle(item, [impossible, *rows]).tier == "frontier"


def test_calibration_receipt_cannot_survive_changed_actual_model_binding():
    """Trusted dev receipt is reused after a model changes while caller config label stays same."""
    rig = Rig()
    rig.context.cheap_model = "different-model"
    decision = Policy("cascade_jev", calibration=permit()).route(rig.item, rig.context)
    assert decision.reasons == ["checker_unvalidated"] and decision.tier == "frontier"
    assert rig.calls == []


def test_random_is_seeded_eligible_and_oracle_is_unreachable_from_policy_imports():
    """Random chooses unavailable code or executable policy imports scorer/oracle data."""
    item = RouteInput(prompt_id="unit", text="Outside bounded grammar")
    weights = {"code": 100.0, "cheap": 1.0, "frontier": 1.0}
    assert random_reference(item, seed=7, weights=weights) == random_reference(
        item, seed=7, weights=weights
    )
    assert {random_reference(item, seed=n, weights=weights) for n in range(30)} == {
        "cheap",
        "frontier",
    }
    with pytest.raises(ValueError):
        random_reference(item, seed=1, weights={"code": 1.0})
    root = Path(__file__).resolve().parents[2] / "src/llm_router_eval/shared/routing"
    for source in [root / "policies/__init__.py", root / "context/__init__.py"]:
        imports = [
            n.module or ""
            for n in ast.walk(ast.parse(source.read_text()))
            if isinstance(n, ast.ImportFrom)
        ]
        assert not any("references" in name or "scoring" in name for name in imports)
