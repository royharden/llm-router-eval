"""Reference selection, incomplete evidence and reporting-only boundary regressions."""

import json
import socket
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from llm_router_eval.evaluation import engine, workflow
from llm_router_eval.evaluation.references import ORACLE, RANDOM_GRID, oracle_tier, random_tier
from llm_router_eval.shared.providers import Provider

ROOT = Path(__file__).resolve().parents[2]
TASKS = ROOT / "fixtures/implementation-controls/tasks.json"
VECTORS = ROOT / "fixtures/implementation-controls/requests.json"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*a, **kw):
        raise RuntimeError("reference control forbids network")

    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)


def candidate(passed, cost):
    return SimpleNamespace(correct=passed), {"source_point_usd": cost}, set()


def test_oracle_selects_cheapest_pass_then_cheapest_failure_and_stable_ties():
    """what_bug_this_catches: oracle selects a cheap failure or invents a no-pass success."""
    rows = dict(
        code=candidate(False, "0"), cheap=candidate(True, "0.1"), frontier=candidate(True, "0.2")
    )
    assert oracle_tier(rows) == ("cheap", None)
    rows["cheap"] = candidate(False, "0.1")
    assert oracle_tier(rows) == ("frontier", None)
    rows["frontier"] = candidate(False, "0.2")
    assert oracle_tier(rows) == ("code", "no_passing_tier")
    assert oracle_tier(dict(frontier=candidate(True, "0"), cheap=candidate(True, "0"))) == (
        "cheap",
        None,
    )


@pytest.mark.parametrize(
    "unavailable,reason",
    [(None, "missing_observation"), (candidate(True, None), "unknown_cost_rank")],
)
def test_oracle_cannot_claim_minimum_from_missing_observations_or_prices(unavailable, reason):
    """what_bug_this_catches: an incomplete eligible tier is quietly excluded from the oracle."""
    assert oracle_tier(dict(cheap=candidate(True, "0.1"), frontier=unavailable)) == (None, reason)


def test_seeded_grid_never_samples_ineligible_code_and_ignores_iteration_order():
    """what_bug_this_catches: random references depend on input order or use unsupported code."""
    ids = [f"public-{i}" for i in range(100)]
    for weights in RANDOM_GRID.values():
        draws = {i: random_tier(i, ("cheap", "frontier"), weights) for i in ids}
        assert draws == {i: random_tier(i, ("frontier", "cheap"), weights) for i in reversed(ids)}
        assert set(draws.values()) == {"cheap", "frontier"}
        assert {random_tier(i, ("code", "cheap", "frontier"), weights) for i in ids} == {
            "code",
            "cheap",
            "frontier",
        }


def test_actual_references_reuse_existing_matrix_without_extra_calls_or_private_export(
    tmp_path, monkeypatch
):
    """what_bug_this_catches: report-only references trigger calls or leak private per-ID rows."""
    calls = []
    original = Provider.run

    def counted(self, *a, **kw):
        calls.append(kw["context"].task_id)
        return original(self, *a, **kw)

    monkeypatch.setattr(Provider, "run", counted)
    report = workflow.run_offline(TASKS, VECTORS, tmp_path / "normal")
    refs = report["references"]
    assert len(calls) == 60 and report["ledger"]["dispatched_attempts"] == 0
    assert refs["case_N"] == refs["family_N"] == 8
    assert [r["reference"] for r in refs["rows"]] == [*RANDOM_GRID, ORACLE]
    for row in refs["rows"]:
        assert row["metric"]["corpus_N"] == row["metric"]["denominator"] == 8
        assert sum(row["selected_tier_counts"].values()) == 8
        assert row["status"] == "complete" and row["unavailable_N"] == 0
        assert row["cost_per_assigned_case_ratio"]["cases"] == 8
        assert row["source_cost"]["current_external_cost_usd"] == "0"
    oracle = refs["rows"][-1]
    cheap = next(
        r
        for r in report["cohorts"]["deterministic"]["source_costs"]
        if r["method"] == "always_cheap"
    )
    assert oracle["metric"]["numerator"] == 8
    assert Decimal(oracle["source_cost"]["source_point_usd"]) <= Decimal(cheap["source_point_usd"])
    assert refs["host_resource_cost_usd"] is None
    serialized = json.dumps(refs)
    for task_id in set(calls):
        assert task_id not in serialized
    assert "Counterfactual references" in (tmp_path / "normal/report.md").read_text()
    # A consumer editing its retained report must not retune the module's next invocation.
    original_definition = refs["definition_sha256"]
    refs["definition"]["random_integer_weights"]["random_uniform"] = (100, 0, 0)
    next_report = workflow.run_offline(TASKS, VECTORS, tmp_path / "next")
    assert next_report["references"]["definition_sha256"] == original_definition
    assert len(calls) == 120


@pytest.mark.parametrize("scenario", ["unknown_cost", "collection_failure"])
def test_unknown_or_missing_sources_keep_assigned_cases_and_null_reference_costs(
    tmp_path, monkeypatch, scenario
):
    """what_bug_this_catches: missing ranks turn into complete zero-cost oracle results."""
    if scenario == "collection_failure":

        def fail(*a, **kw):
            raise ValueError("explicit reference source failure")

        monkeypatch.setattr(engine, "collect_matrix", fail)
    report = workflow.run_offline(
        TASKS,
        VECTORS,
        tmp_path / "missing",
        scenario="unknown_cost" if scenario == "unknown_cost" else "normal",
    )
    oracle = report["references"]["rows"][-1]
    assert oracle["reference"] == ORACLE and oracle["unavailable_N"] == 8
    assert oracle["metric"]["corpus_N"] == oracle["metric"]["denominator"] == 8
    assert oracle["metric"]["error_N"] == 8
    assert oracle["source_cost"]["source_point_usd"] is None
    assert oracle["source_cost"]["source_upper_usd"] is None
    assert oracle["status"] == "partial" and oracle["selected_tier_counts"] == {}


@pytest.mark.parametrize("method,missing", [("always_frontier", 8), ("rules", 2)])
def test_disabled_baseline_is_not_silently_excluded_from_eligible_tiers(
    tmp_path, monkeypatch, method, missing
):
    """what_bug_this_catches: a disabled baseline improves oracle quality by disappearing."""
    original = workflow.run_comparison

    def disabled(view, source, policies):
        return original(view, source, policies, disabled_methods={method})

    monkeypatch.setattr(workflow, "run_comparison", disabled)
    report = workflow.run_offline(TASKS, VECTORS, tmp_path / "disabled")
    rows = report["references"]["rows"]
    assert rows[-1]["unavailable_N"] == missing
    assert rows[-1]["reason_counts"] == {"missing_observation": missing}
    assert any(r["unavailable_N"] > 0 for r in rows[:-1])
    assert all(sum(r["selected_tier_counts"].values()) + r["unavailable_N"] == 8 for r in rows)


def test_rubric_only_population_never_gets_random_or_oracle_quality(tmp_path):
    """what_bug_this_catches: an unvalidated rubric receives oracle correctness or a fake point."""
    data = json.loads(TASKS.read_bytes())
    data["tasks"] = [t for t in data["tasks"] if t["kind"] == "rubric"]
    tasks = tmp_path / "rubric-only.json"
    tasks.write_text(json.dumps(data), encoding="utf-8")
    report = workflow.run_offline(tasks, VECTORS, tmp_path / "rubric")
    assert report["references"]["status"] == "empty"
    assert report["references"]["case_N"] == 0 and report["references"]["rows"] == []
