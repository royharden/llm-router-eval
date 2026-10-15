"""Original adversarial scorer controls; no human/evaluation labels."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from llm_router_eval.shared.callledger.models import canonical_digest as h
from llm_router_eval.shared.routing.scoring import (
    CandidateAnswer,
    ScoreRecord,
    ScoringLabel,
    score,
    summarize_scores,
)
from llm_router_eval.shared.routing.scoring.labels import (
    HumanAdmission,
    HumanLabelFile,
    PairSpec,
    admit_human_labels,
    blank_template,
    canonical_json,
)


@pytest.fixture(autouse=True)
def bug_metadata(request):
    request.node.user_properties.append(("what_bug_this_catches", request.node.function.__doc__))


def label(kind="gsm8k", answer="7", prompt_id="unit"):
    return ScoringLabel(
        prompt_id=prompt_id,
        family_id="unit-family",
        kind=kind,
        answer=None if kind == "rubric" else answer,
        rubric=["Clearly explains the concept."] if kind == "rubric" else [],
    )


def candidate(text, status="ok", prompt_id="unit"):
    return CandidateAnswer(prompt_id=prompt_id, status=status, text=text)


@pytest.mark.parametrize(
    "kind,gold,text,passed",
    [
        ("gsm8k", "7", "FINAL: 7", True),
        ("gsm8k", "7", "The working contains 7.\nFINAL: 8", False),
        ("gsm8k", "7", "FINAL: 8\nThe correct answer is 7", False),
        ("gsm8k", "7", "FINAL: 7\nFINAL: 8", False),
        ("gsm8k", "7", "7", False),
        ("gsm8k", "7", "FINAL: 7 or 8", False),
        ("gsm8k", "1234", "FINAL: 1,234", False),
        ("gsm8k", "1.23", "FINAL: 1,23", False),
        ("gsm8k", "7", "FINAL: ７", False),
        ("gsm8k", "7", "FINAL: 7e0", False),
        ("gsm8k", "0", "FINAL: -0.0", True),
        ("gsm8k", "7.00", "work\r\nFINAL: +7.0\r\n", True),
        ("gsm8k", "7", "FINAL: NaN", False),
        ("gsm8k", "7", "FINAL: Infinity", False),
        ("gsm8k", "7", "FINAL: 7 dollars", False),
        ("mmlu", "A", "The working mentions A.\nFINAL: B", False),
        ("mmlu", "A", "FINAL: A", True),
        ("mmlu", "A", "FINAL: (A)", False),
        ("mmlu", "A", "FINAL: A or B", False),
        ("mmlu", "A", "FINAL: a", False),
        ("code", "6", "FINAL: 6", True),
        ("code", "6", "FINAL: 6.0", False),
        ("code", "6", "FINAL: __import__('os')", False),
        ("exact", "Cobalt", "FINAL: Cobalt", True),
        ("exact", "Cobalt", "FINAL: cobalt", False),
        ("exact", "Cobalt", "FINAL: Cobalt.", False),
        ("exact", "Cobalt", "FINAL: Cobalt ", False),
    ],
)
def test_final_answer_grammar_rejects_planted_working_and_ambiguous_formats(
    kind, gold, text, passed
):
    """A correct token in working, locale ambiguity or punctuation yields an invented pass."""
    result = score(label(kind, gold), candidate(text))
    assert result.passed is passed and result.status == ("passed" if passed else "failed")


@pytest.mark.parametrize(
    "status",
    [
        "provider_error",
        "refused",
        "invalid_output",
        "budget_rejected",
        "unknown",
        "cache_miss",
        "deadline_exceeded",
        "cancelled",
        "unsupported",
    ],
)
def test_assigned_provider_failures_cannot_pass_even_with_correct_text(status):
    """Failed/budget/unknown provider rows are dropped or scored using unusable text."""
    result = score(label(), candidate("FINAL: 7", status))
    assert result.status == "failed" and result.reason == "provider_failed"


def test_unvalidated_rubric_missing_records_and_provider_failures_keep_denominators():
    """Unvalidated rubric/missing rows vanish from assigned counts or become fabricated passes."""
    records = [
        score(label(prompt_id="a"), candidate("FINAL: 7", prompt_id="a")),
        score(label("rubric", prompt_id="b"), candidate("An explanation", prompt_id="b")),
        score(label(prompt_id="c"), candidate(None, status="budget_rejected", prompt_id="c")),
    ]
    summary = summarize_scores(records, assigned_ids=["a", "b", "c", "d"])
    assert summary["assigned"] == 4 and summary["scored"] == 2 and summary["passed"] == 1
    assert summary["failed"] == summary["unscored"] == summary["missing"] == 1
    assert summary["passes_per_assigned"] == 0.25 and summary["completeness"] == "incomplete"
    with pytest.raises(ValueError):
        summarize_scores([records[0], records[0]], assigned_ids=["a"])


def test_label_candidate_binding_invalid_gold_and_private_output_projection():
    """Scorer uses another task's label or persists private gold/rubric in a score row."""
    with pytest.raises(ValueError, match="score_item_binding"):
        score(label(), candidate("FINAL: 7", prompt_id="other"))
    for kind, gold in [("gsm8k", "1,234"), ("mmlu", "E"), ("code", "1.0")]:
        with pytest.raises(ValueError):
            label(kind, gold)
    result = score(label("exact", "PRIVATE-GOLD-CANARY"), candidate("FINAL: wrong"))
    assert "PRIVATE-GOLD-CANARY" not in result.model_dump_json()
    assert result.reason == "wrong_answer"


def pairs():
    return [
        PairSpec(
            pair_id=f"unit-pair-{i}",
            prompt_id=f"unit-prompt-{i // 3}",
            candidate_source=["cheap", "frontier", "corruption"][i % 3],
            response_sha256=h(["synthetic response", i]),
        )
        for i in range(6)
    ]


def trust_double():
    values = pairs()
    template = json.loads(blank_template(values))
    value = HumanLabelFile(
        schema_version=1,
        evidence_kind="blinded_human",
        human_id="UNIT-human-trust-double",
        blind_manifest_sha256=template["blind_manifest_sha256"],
        labels=[dict(pair_id=p.pair_id, passed=i % 2 == 0) for i, p in enumerate(values)],
    )
    payload = canonical_json(value.model_dump(mode="json"))
    admission = HumanAdmission(
        label_file_sha256=hashlib.sha256(payload).hexdigest(),
        blind_manifest_sha256=value.blind_manifest_sha256,
        pair_manifest_sha256=h([p.model_dump(mode="json") for p in values]),
        human_id=value.human_id,
        owner_receipt_sha256=h("UNIT owner receipt"),
        expected_pair_ids=[p.pair_id for p in values],
        excluded_agent_ids=["UNIT-agent-author"],
    )
    return values, payload, admission


def test_blank_template_contains_no_guessed_judge_or_human_labels():
    """An agent-generated template fills fake human answers or leaks judge outcomes."""
    first = blank_template(pairs())
    assert first == blank_template(pairs())
    value = json.loads(first)
    assert all(row["passed"] is None for row in value["labels"])
    assert set(value) == {"schema_version", "kind", "blind_manifest_sha256", "labels"}
    assert all(set(row) == {"pair_id", "passed"} for row in value["labels"])
    with pytest.raises(ValueError):
        HumanLabelFile.model_validate(value)


@pytest.mark.parametrize(
    "mutation", ["bytes", "synthetic", "human", "excluded", "coverage", "manifest"]
)
def test_human_admission_needs_actual_external_exact_trust_record(mutation):
    """Synthetic/source-edited/agent-authored/incomplete labels self-label as blinded human data."""
    values, payload, admission = trust_double()
    assert len(admit_human_labels(payload, admission=admission, pairs=values).labels) == 6
    if mutation == "bytes":
        payload += b" "
    elif mutation == "human":
        admission = admission.model_copy(update={"human_id": "different-human"})
    elif mutation == "excluded":
        admission = admission.model_copy(update={"excluded_agent_ids": [admission.human_id]})
    elif mutation == "coverage":
        admission = admission.model_copy(update={"expected_pair_ids": [values[0].pair_id]})
    elif mutation == "manifest":
        admission = admission.model_copy(update={"blind_manifest_sha256": h("wrong")})
    else:
        value = json.loads(payload)
        value["evidence_kind"] = "synthetic_implementation"
        payload = canonical_json(value)
        admission = admission.model_copy(
            update={"label_file_sha256": hashlib.sha256(payload).hexdigest()}
        )
    with pytest.raises(ValueError):
        admit_human_labels(payload, admission=admission, pairs=values)


def test_template_cli_creates_only_blank_output_and_refuses_overwrite(tmp_path):
    """Label helper overwrites owner work or silently fabricates completed labels."""
    input_path = tmp_path / "pairs.json"
    output = tmp_path / "blank.json"
    input_path.write_bytes(canonical_json([p.model_dump(mode="json") for p in pairs()]))
    root = Path(__file__).resolve().parents[2]
    argv = [
        sys.executable,
        "scripts/label_judge.py",
        "--pairs",
        str(input_path),
        "--output",
        str(output),
    ]
    first = subprocess.run(argv, cwd=root, capture_output=True, timeout=20)
    assert first.returncode == 0 and all(
        row["passed"] is None for row in json.loads(output.read_bytes())["labels"]
    )
    original = output.read_bytes()
    second = subprocess.run(argv, cwd=root, capture_output=True, timeout=20)
    assert second.returncode != 0 and output.read_bytes() == original


def test_cohort_relabel_cannot_reuse_blind_human_admission():
    """Changing corruption into natural cheap responses silently inflates natural-only gates."""
    values, payload, admission = trust_double()
    values[2] = values[2].model_copy(update={"candidate_source": "cheap"})
    with pytest.raises(ValueError, match="pair_manifest"):
        admit_human_labels(payload, admission=admission, pairs=values)


def test_score_reason_cannot_claim_pass_for_a_provider_failure():
    """A persisted score row has contradictory pass status and failure reason."""
    with pytest.raises(ValueError, match="score_reason"):
        ScoreRecord(
            prompt_id="unit",
            family_id="family",
            kind="exact",
            status="passed",
            passed=True,
            reason="provider_failed",
        )
