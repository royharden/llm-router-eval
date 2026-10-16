"""Actual headless figures, fixed populations, canonical sampling and unavailable-data controls."""

import hashlib
import json
import socket
from pathlib import Path
from xml.etree import ElementTree

import pytest
from PIL import Image

from llm_router_eval.data.corpus import canonical_bytes, digest
from llm_router_eval.evaluation import engine, workflow
from llm_router_eval.evaluation.plotting import figure_data, render_figures
from llm_router_eval.shared.providers import Provider

ROOT = Path(__file__).resolve().parents[2]
TASKS = ROOT / "fixtures/implementation-controls/tasks.json"
VECTORS = ROOT / "fixtures/implementation-controls/requests.json"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*a, **kw):
        raise RuntimeError("figure control forbids network")

    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)


def tasks_file(tmp_path, rows):
    data = json.loads(TASKS.read_bytes())
    data["tasks"] = rows
    for row in rows:
        row["row_sha256"] = digest({k: v for k, v in row.items() if k != "row_sha256"})
    path = tmp_path / "tasks.json"
    path.write_bytes(canonical_bytes(data))
    return path


def test_real_png_svg_and_table_reproduce_from_the_same_retained_report(tmp_path, monkeypatch):
    """what_bug_this_catches: plots recollect sources, misalign cases or encode volatile IDs."""
    calls = []
    original = Provider.run

    def counted(self, *a, **kw):
        calls.append(kw["context"].task_id)
        return original(self, *a, **kw)

    monkeypatch.setattr(Provider, "run", counted)
    target = tmp_path / "normal"
    report = workflow.run_offline(TASKS, VECTORS, target, plots=True)
    assert len(calls) == 60 and report["ledger"]["dispatched_attempts"] == 0
    sample = report["sampling"]
    assert sample["case_N"] == sample["family_N"] == 8
    assert sample["draws"] == 2000 and sample["level"] == 0.95 and sample["seed"] == 20261014
    assert len(sample["methods"]) == 11
    for estimate in sample["methods"].values():
        assert estimate["valid_draws"] == 2000 and estimate["null_reason"] is None
        assert estimate["interval"]["draw_ids_sha256"] == sample["draws_sha256"]
    data = json.loads((target / "figure-data.json").read_bytes())
    assert data["eligible_N"] == 8 and len(data["points"]) == 11 and data["omitted"] == []
    assert all(row["n"] == 8 for row in data["local_timing"])
    assert data["model_latency_ms"] is data["reference_latency_ms"] is None
    assert data["host_resource_cost_usd"] is None
    for name, value in render_figures(report).items():
        assert value == (target / name).read_bytes()
        assert hashlib.sha256(value).hexdigest() == report["figures"][name]
    with Image.open(target / "quality-cost.png") as image:
        assert image.size == (3120, 1680) and image.format == "PNG"
    root = ElementTree.fromstring((target / "quality-cost.svg").read_bytes())
    assert root.tag.endswith("svg")
    svg = (target / "quality-cost.svg").read_text(encoding="utf-8")
    assert "AUTHORED SYNTHETIC CONTROLS" in svg and "Local timing only" in svg
    assert "2000" in json.dumps(data) and "2,000 canonical draws" in svg
    assert "dc:date" not in svg
    for task in set(calls):
        assert task not in svg and task not in json.dumps(data)


@pytest.mark.parametrize("scenario", ["unknown_cost", "collection_failure"])
def test_missing_costs_never_get_zero_coordinate_points(tmp_path, monkeypatch, scenario):
    """what_bug_this_catches: unavailable source costs become fake zero-dollar plot points."""
    if scenario == "collection_failure":

        def fail(*a, **kw):
            raise ValueError("declared figure source failure")

        monkeypatch.setattr(engine, "collect_matrix", fail)
    report = workflow.run_offline(
        TASKS,
        VECTORS,
        tmp_path / "unknown",
        plots=True,
        scenario="unknown_cost" if scenario == "unknown_cost" else "normal",
    )
    data = figure_data(report)
    assert data["points"] == [] and len(data["omitted"]) == 11
    assert all(row["reason"] == "unknown_source_cost" for row in data["omitted"])
    assert all(row["source_cost_upper_usd"] is None for row in data["omitted"])
    assert report["sampling"]["case_N"] == 8
    assert report["ledger"]["known_spend_usd"] == "0"
    assert "Unknown costs are omitted" in (tmp_path / "unknown/quality-cost.svg").read_text()


def test_actual_code_only_zero_model_consumption_can_be_plotted(tmp_path):
    """what_bug_this_catches: an honest code-only zero model cost is confused with unknown cost."""
    rows = [r for r in json.loads(TASKS.read_bytes())["tasks"] if r["kind"] == "code"]
    report = workflow.run_offline(
        tasks_file(tmp_path, rows), VECTORS, tmp_path / "code", plots=True, scenario="unknown_cost"
    )
    data = figure_data(report)
    assert len(data["points"]) == 1
    assert data["points"][0]["method"] == "rules"
    assert data["points"][0]["source_usd_per_case"] == 0
    assert data["host_resource_cost_usd"] is None and data["eligible_N"] == 2


def test_one_family_has_no_invented_interval_or_private_family_export(tmp_path):
    """what_bug_this_catches: one family is treated as independent cases or exported by name."""
    rows = [r for r in json.loads(TASKS.read_bytes())["tasks"] if r["kind"] == "gsm8k"]
    for row in rows:
        row["family_id"] = "PRIVATE-FAMILY-PLOT-CANARY"
    report = workflow.run_offline(tasks_file(tmp_path, rows), VECTORS, tmp_path / "one", plots=True)
    assert report["sampling"]["family_N"] == 1 and report["sampling"]["case_N"] == 2
    assert all(
        r["interval"] is None and r["null_reason"] == "insufficient_independent_groups"
        for r in report["sampling"]["methods"].values()
    )
    assert b"PRIVATE-FAMILY" not in (tmp_path / "one/figure-data.json").read_bytes()
    assert b"PRIVATE-FAMILY" not in (tmp_path / "one/quality-cost.svg").read_bytes()


def test_rubric_only_has_empty_points_and_no_fake_sampling(tmp_path):
    """what_bug_this_catches: unvalidated rubric items acquire plotted deterministic quality."""
    rows = [r for r in json.loads(TASKS.read_bytes())["tasks"] if r["kind"] == "rubric"]
    report = workflow.run_offline(
        tasks_file(tmp_path, rows), VECTORS, tmp_path / "rubric", plots=True
    )
    assert report["sampling"]["status"] == "empty" and report["sampling"]["methods"] == {}
    assert figure_data(report)["points"] == []
    assert figure_data(report)["local_timing"] == []


def test_disabled_method_stays_unavailable_for_figures_and_sampling(tmp_path, monkeypatch):
    """what_bug_this_catches: a disabled policy gets a measured latency or confidence interval."""
    original = workflow.run_comparison

    def disabled(view, source, policies):
        return original(view, source, policies, disabled_methods={"always_frontier"})

    monkeypatch.setattr(workflow, "run_comparison", disabled)
    report = workflow.run_offline(TASKS, VECTORS, tmp_path / "disabled", plots=True)
    assert "always_frontier" not in report["sampling"]["methods"]
    assert any(
        r["method"] == "always_frontier" and r["reason"] == "not_run"
        for r in figure_data(report)["omitted"]
    )
    assert not any(r["method"] == "always_frontier" for r in figure_data(report)["local_timing"])


def test_zero_passes_remain_zero_with_no_fake_success_or_cost_per_pass(tmp_path):
    """what_bug_this_catches: plotting transforms zero-pass quality into a perfect/missing row."""
    rows = json.loads(TASKS.read_bytes())["tasks"]
    for row in rows:
        if row["kind"] != "rubric":
            row["scoring"]["answer"] = (
                "B"
                if row["kind"] == "mmlu"
                else "999"
                if row["kind"] in {"gsm8k", "code"}
                else "WRONG"
            )
    report = workflow.run_offline(
        tasks_file(tmp_path, rows), VECTORS, tmp_path / "wrong", plots=True
    )
    assert len(figure_data(report)["points"]) == 11
    assert all(r["pass_fraction"] == 0 for r in figure_data(report)["points"])
    assert all(
        r["cost_per_pass_undefined_reason"] == "zero_passes"
        for r in report["cohorts"]["deterministic"]["source_costs"]
    )


@pytest.mark.parametrize("mutation", ["private_method", "nonfinite_cost"])
def test_malformed_aggregate_points_refuse_before_rendering(tmp_path, mutation):
    """what_bug_this_catches: unallowlisted names or nonfinite money reach the figure renderer."""
    report = workflow.run_offline(TASKS, VECTORS, tmp_path / "base")
    if mutation == "private_method":
        report["cohorts"]["deterministic"]["metrics"][0]["method"] = "PRIVATE-PLOT-CANARY"
    else:
        report["cohorts"]["deterministic"]["source_costs"][0]["source_point_usd"] = "NaN"
    with pytest.raises(ValueError, match="plot"):
        figure_data(report)
