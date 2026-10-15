"""Fixed cohort denominators and source-cost/privacy regression controls."""

import json
import socket
from decimal import Decimal
from pathlib import Path

import pytest

from llm_router_eval.data.corpus import canonical_bytes, digest
from llm_router_eval.evaluation import engine, workflow
from llm_router_eval.evaluation.report import COHORT_KINDS, render_json, render_markdown
from llm_router_eval.shared.providers import Provider

ROOT = Path(__file__).resolve().parents[2]
TASKS = ROOT / "fixtures/implementation-controls/tasks.json"
VECTORS = ROOT / "fixtures/implementation-controls/requests.json"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*a, **kw):
        raise RuntimeError("cohort control forbids network")

    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)


def test_cohort_cost_and_accuracy_share_the_same_fixed_population(tmp_path, monkeypatch):
    """what_bug_this_catches: ten-case cost is paired with eight-case accuracy without a label."""
    calls = []
    original = Provider.run

    def counted(self, *a, **kw):
        calls.append(kw["context"].task_id)
        return original(self, *a, **kw)

    monkeypatch.setattr(Provider, "run", counted)
    report = workflow.run_offline(TASKS, VECTORS, tmp_path / "normal")
    assert len(calls) == 60 and report["ledger"]["dispatched_attempts"] == 0
    deterministic = report["cohorts"]["deterministic"]
    assert deterministic["case_N"] == deterministic["eligible_N"] == 8
    assert deterministic["unscored_N"] == 0
    assert all(row["corpus_N"] == row["denominator"] == 8 for row in deterministic["metrics"])
    total = next(
        row
        for row in report["counterfactual_policy_consumption"]
        if row["method"] == "always_cheap"
    )
    selected = next(row for row in deterministic["source_costs"] if row["method"] == "always_cheap")
    assert Decimal(total["source_point_usd"]) == Decimal("0.010")
    assert Decimal(selected["source_point_usd"]) == Decimal("0.008")
    assert selected["cost_per_assigned_case_ratio"] == dict(
        cost_usd=selected["source_point_usd"], cases=8
    )
    assert [row["kind"] for row in report["cohorts"]["kinds"]] == list(COHORT_KINDS)
    assert report["cohorts"]["absent_kinds"] == []
    kinds = report["cohorts"]["kinds"]
    assert sum(cohort["case_N"] for cohort in kinds) == 10
    assert all(cohort["case_N"] == cohort["family_N"] == 2 for cohort in kinds)
    for all_cost in report["counterfactual_policy_consumption"]:
        parts = [
            next(row for row in cohort["source_costs"] if row["method"] == all_cost["method"])
            for cohort in kinds
        ]
        assert sum((Decimal(row["source_point_usd"]) for row in parts), Decimal(0)) == Decimal(
            all_cost["source_point_usd"]
        )
    rubric = next(cohort for cohort in kinds if cohort["kind"] == "rubric")
    assert rubric["eligible_N"] == 0 and rubric["unscored_N"] == 2
    assert all(row["value"] is None and row["denominator"] == 0 for row in rubric["metrics"])
    assert all(
        row["cost_per_pass_undefined_reason"] == "no_eligible_cases"
        for row in rubric["source_costs"]
    )
    assert (tmp_path / "normal/report.md").read_bytes() == render_markdown(report)


@pytest.mark.parametrize("scenario", ["unknown_cost", "collection_failure"])
def test_cohort_unavailable_cost_never_becomes_a_zero_point(tmp_path, scenario, monkeypatch):
    """what_bug_this_catches: cohort grouping invents zero cost for used unknown model sources."""
    if scenario == "collection_failure":

        def fail(*a, **kw):
            raise ValueError("declared local missing source control")

        monkeypatch.setattr(engine, "collect_matrix", fail)
    report = workflow.run_offline(
        TASKS,
        VECTORS,
        tmp_path / "unknown",
        scenario="unknown_cost" if scenario == "unknown_cost" else "normal",
    )
    for cohort in [report["cohorts"]["deterministic"], *report["cohorts"]["kinds"]]:
        for row in cohort["source_costs"]:
            if (
                scenario == "unknown_cost"
                and cohort.get("kind") == "code"
                and row["method"] == "rules"
            ):
                assert row["source_point_usd"] == row["source_upper_usd"] == "0"
                assert row["observed_source_cells"] == 0
                assert cohort["cost_scope"] == "model_source_only"
                assert cohort["host_resource_cost_usd"] is None
            else:
                assert row["source_point_usd"] is None and row["source_upper_usd"] is None
    assert report["ledger"]["known_spend_usd"] == "0"


def test_private_source_and_family_names_do_not_enter_cohort_exports(tmp_path):
    """what_bug_this_catches: grouping leaks private identifiers or changes requests."""
    first = workflow.run_offline(TASKS, VECTORS, tmp_path / "first")
    data = json.loads(TASKS.read_bytes())
    data["sources"][0]["source_id"] = "PRIVATE-SOURCE-CANARY"
    for index, row in enumerate(data["tasks"]):
        row["source_id"] = "PRIVATE-SOURCE-CANARY"
        row["family_id"] = "PRIVATE-FAMILY-CANARY-" + str(index)
        row["row_sha256"] = digest({k: v for k, v in row.items() if k != "row_sha256"})
    tasks = tmp_path / "private-names.json"
    tasks.write_bytes(canonical_bytes(data))
    assert b"PRIVATE-" in tasks.read_bytes()
    second = workflow.run_offline(tasks, VECTORS, tmp_path / "second")
    assert first["source_book_sha256"] == second["source_book_sha256"]
    assert first["cohorts"] == second["cohorts"]
    assert b"PRIVATE-" not in render_json(second) + render_markdown(second)


def test_rubric_only_input_has_an_explicit_empty_deterministic_cohort(tmp_path):
    """what_bug_this_catches: an empty eligible cohort invents an accuracy/cost point."""
    data = json.loads(TASKS.read_bytes())
    data["tasks"] = [r for r in data["tasks"] if r["kind"] == "rubric"]
    tasks = tmp_path / "rubric-only.json"
    tasks.write_bytes(canonical_bytes(data))
    report = workflow.run_offline(tasks, VECTORS, tmp_path / "rubric")
    cohort = report["cohorts"]["deterministic"]
    assert cohort["status"] == "empty" and cohort["case_N"] == 0
    assert cohort["metrics"] == cohort["source_costs"] == []
    assert report["cohorts"]["absent_kinds"] == ["gsm8k", "mmlu", "code", "exact"]
    assert all(
        row["cost_per_pass_undefined_reason"] == "no_eligible_cases"
        for row in report["counterfactual_policy_consumption"]
    )


def test_disabled_method_remains_not_run_in_every_cohort(tmp_path, monkeypatch):
    """what_bug_this_catches: a cohort invents measured costs or outputs for a disabled method."""
    original = workflow.run_comparison

    def disabled(view, source, policies):
        return original(view, source, policies, disabled_methods={"rules"})

    monkeypatch.setattr(workflow, "run_comparison", disabled)
    report = workflow.run_offline(TASKS, VECTORS, tmp_path / "disabled")
    for cohort in [report["cohorts"]["deterministic"], *report["cohorts"]["kinds"]]:
        metric = next(row for row in cohort["metrics"] if row["method"] == "rules")
        cost = next(row for row in cohort["source_costs"] if row["method"] == "rules")
        assert metric["null_reason"] == "not_run" and metric["value"] is None
        assert cost["status"] == "not_run" and cost["observed_source_cells"] == 0
        assert (
            cost["source_point_usd"] is None and cost["cost_per_pass_undefined_reason"] == "not_run"
        )
