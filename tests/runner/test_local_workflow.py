"""Runnable authored workflow, output privacy and honest refusal regressions."""

import hashlib
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from llm_router_eval.cli import main
from llm_router_eval.data.corpus import canonical_bytes, digest
from llm_router_eval.evaluation import engine, workflow
from llm_router_eval.evaluation.report import cost_summary, render_json, render_markdown
from llm_router_eval.evaluation.workflow import run_offline

ROOT = Path(__file__).resolve().parents[2]
TASKS = ROOT / "fixtures/implementation-controls/tasks.json"
VECTORS = ROOT / "fixtures/implementation-controls/requests.json"


@pytest.fixture(autouse=True)
def network_denied(monkeypatch):
    def denied(*a, **kw):
        raise RuntimeError("workflow network forbidden")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


@pytest.mark.parametrize(
    "scenario", ["normal", "code", "cheap", "escalation", "malformed", "budget", "unknown_cost"]
)
def test_local_scenarios_are_actual_offline_paths(tmp_path, scenario, monkeypatch, request):
    request.node.user_properties.append(
        (
            "what_bug_this_catches",
            "Malformed wire must decode to a failed capture without losing all matrix cells; "
            "Source/current cost, unvalidated rubric and actual zero-dispatch stay distinct.",
        )
    )
    decoded = []
    original = workflow.decode_outcome

    def observed(body, **kwargs):
        value = original(body, **kwargs)
        decoded.append((body, value.status))
        return value

    monkeypatch.setattr(workflow, "decode_outcome", observed)
    target = tmp_path / "output"
    report = run_offline(TASKS, VECTORS, target, scenario=scenario)
    assert report["ledger"]["dispatched_attempts"] == 0
    assert report["ledger"]["known_spend_usd"] == report["ledger"]["total_exposure_usd"] == "0"
    assert (
        report["corpus_N"] == 10
        and report["deterministic_eligible_N"] == 8
        and report["rubric_unscored_N"] == 2
    )
    assert len(report["metrics"]) == 7
    assert report["collection_cost"]["observed_source_cells"] == 60
    assert report["evidence_origin"] == "synthetic"
    assert report["scenario_observations"]["code_executions"] > 0
    assert report["scenario_observations"]["cheap_selections"] > 0
    assert report["scenario_observations"]["unvalidated_checker_escalations"] == 20
    if scenario == "malformed":
        assert report["scenario_observations"]["malformed_classifier_escalations"] == 10
        assert len(decoded) == 10 and all(status == "invalid_output" for _, status in decoded)
        assert all(
            "INVALID" in json.loads(body)["output"][0]["content"][0]["text"] for body, _ in decoded
        )
        assert report["source_status_counts"] == dict(ok=50, invalid_output=10)
    if scenario == "budget":
        assert report["virtual_budget_control"]["status"] == "budget_rejected"
        assert report["virtual_budget_control"]["mode"] == "simulation"
        assert report["virtual_budget_control"]["actual_model_requests"] == 0
        assert report["virtual_budget_control"]["provider_result_created"] is False
    if scenario == "unknown_cost":
        assert report["collection_cost"]["source_point_usd"] is None
        assert report["collection_cost"]["source_upper_usd"] is None
        assert report["collection_cost"]["source_unknown_cells"] == 60
    assert (target / "report.json").read_bytes() == render_json(report)
    assert (target / "report.md").read_bytes() == render_markdown(report)
    assert render_json(json.loads(render_json(report))) == render_json(report)
    assert (target / "accounting.sqlite3").is_file()


def test_collection_failure_does_not_invent_zero_source_cost(tmp_path, monkeypatch):
    """what_bug_this_catches: a lost partial matrix misleadingly reports free collection."""
    attempts = []

    def stopped(spec, **kwargs):
        attempts.append(spec.item.prompt_id)
        raise ValueError("declared local collection failure control")

    monkeypatch.setattr(engine, "collect_matrix", stopped)
    report = run_offline(TASKS, VECTORS, tmp_path / "failed-collection")
    assert len(attempts) == len(set(attempts)) == 10
    assert report["collection"] == dict(
        status="incomplete", expected_cells=60, retained_cells=0, uncollected_cells=60
    )
    assert report["collection_cost"]["source_point_usd"] is None
    assert report["collection_cost"]["source_upper_usd"] is None
    assert report["ledger"]["dispatched_attempts"] == 0
    assert all(row["error_N"] == 8 and row["denominator"] == 8 for row in report["metrics"])
    assert all(
        row["source_point_usd"] is None for row in report["counterfactual_policy_consumption"]
    )


def test_zero_passes_keeps_cost_per_pass_undefined(tmp_path):
    """what_bug_this_catches: an all-failed population is assigned a zero cost-per-pass."""
    data = json.loads(TASKS.read_bytes())
    for row in data["tasks"]:
        if row["kind"] != "rubric":
            row["scoring"]["answer"] = (
                "B"
                if row["kind"] == "mmlu"
                else "999"
                if row["kind"] in {"gsm8k", "code"}
                else "WRONG"
            )
            row["row_sha256"] = digest({k: v for k, v in row.items() if k != "row_sha256"})
    tasks = tmp_path / "all-wrong-tasks.json"
    tasks.write_bytes(canonical_bytes(data))
    report = run_offline(tasks, VECTORS, tmp_path / "zero-passes")
    assert all(row["numerator"] == 0 and row["denominator"] == 8 for row in report["metrics"])
    assert all(
        row["cost_per_pass_undefined_reason"] == "zero_passes"
        for row in report["counterfactual_policy_consumption"]
    )


def test_cost_subtotal_retains_cents_beyond_default_decimal_precision():
    """what_bug_this_catches: 64-character Money values silently lose cents in report sums."""
    rows = [
        dict(span_id=str(i), source_cost_usd=value, current_external_cost_usd="0")
        for i, value in enumerate(
            ["1000000000000000000000000000000.01", "1000000000000000000000000000000.02"]
        )
    ]
    assert cost_summary(rows)["source_point_usd"] == "2000000000000000000000000000000.03"


def test_existing_output_and_bad_input_refuse_without_overwrite(tmp_path):
    target = tmp_path / "existing"
    target.mkdir()
    sentinel = target / "keep"
    sentinel.write_bytes(b"unchanged")
    with pytest.raises(ValueError, match="output_exists"):
        run_offline(TASKS, VECTORS, target)
    assert sentinel.read_bytes() == b"unchanged" and list(target.iterdir()) == [sentinel]
    malformed = tmp_path / "bad.json"
    malformed.write_bytes(b"{}")
    target = tmp_path / "never-created"
    with pytest.raises(ValueError):
        run_offline(malformed, VECTORS, target)
    assert not target.exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("request", None),
        ("request", []),
        ("request", "not-an-object"),
        ("request", 7),
        ("request", True),
        ("task_id", []),
        ("task_id", {}),
        ("task_id", None),
        ("task_id", 7),
    ],
)
def test_malformed_vector_types_refuse_before_any_state(
    tmp_path, field, value, monkeypatch, capsys, request
):
    """what_bug_this_catches: S3-RV2-001 null requests or unhashable keys crash the CLI."""
    request.node.user_properties.append(
        (
            "what_bug_this_catches",
            "S3-RV2-001: validate JSON request/key types before dereference or hashing; "
            "clean CLI refusal must precede ledger/comparison construction and all output writes.",
        )
    )
    vectors = json.loads(VECTORS.read_bytes())
    vectors["fixtures"][0][field] = value
    path = tmp_path / "invalid-vectors.json"
    path.write_bytes(canonical_bytes(vectors))
    sentinel = tmp_path / "keep"
    sentinel.write_bytes(b"unchanged")
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.iterdir()}

    def no_state(*a, **kw):
        pytest.fail("Malformed ingress reached state/evaluation construction")

    monkeypatch.setattr(workflow, "Ledger", no_state)
    monkeypatch.setattr(workflow, "run_comparison", no_state)
    result = main(
        [
            "eval",
            "--tasks",
            str(TASKS),
            "--vectors",
            str(path),
            "--out",
            str(tmp_path / "never-created"),
        ]
    )
    captured = capsys.readouterr()
    assert result == 2 and json.loads(captured.out) == dict(
        status="refused", reason="invalid_input_or_existing_output"
    )
    assert captured.err == ""
    assert before == {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.iterdir()
    }


def test_null_vector_request_process_returns_closed_refusal(tmp_path):
    """what_bug_this_catches: __main__ leaks a traceback/exit1 for request:null."""
    vectors = json.loads(VECTORS.read_bytes())
    vectors["fixtures"][0]["request"] = None
    path = tmp_path / "null-request.json"
    path.write_bytes(canonical_bytes(vectors))
    before = path.read_bytes()
    output = tmp_path / "never-created"
    env = {
        k: os.environ[k] for k in ["SystemRoot", "WINDIR", "PATH", "TEMP", "TMP"] if k in os.environ
    }
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(ROOT))
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "llm_router_eval",
            "eval",
            "--tasks",
            str(TASKS),
            "--vectors",
            str(path),
            "--out",
            str(output),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 2 and json.loads(result.stdout)["status"] == "refused"
    assert result.stderr == b"" and not output.exists() and path.read_bytes() == before


def test_raw_private_input_canary_never_enters_safe_reports(tmp_path):
    canary = "PRIVATE_CANARY_SHOULD_NEVER_LEAK"
    data = json.loads(TASKS.read_bytes())
    vectors = json.loads(VECTORS.read_bytes())
    for row in data["tasks"]:
        row["prompt"] += " " + canary
        if row["kind"] == "rubric":
            row["scoring"]["rubric"] = [canary]
        row["row_sha256"] = digest({k: v for k, v in row.items() if k != "row_sha256"})
    by_id = {row["task_id"]: row for row in data["tasks"]}
    for row in vectors["fixtures"]:
        row["request"]["messages"][0]["content"][0]["text"] = by_id[row["task_id"]]["prompt"]
    tasks = tmp_path / "tasks.json"
    tasks.write_bytes(canonical_bytes(data))
    inputs = tmp_path / "vectors.json"
    inputs.write_bytes(canonical_bytes(vectors))
    assert canary.encode() in tasks.read_bytes()  # Positive planted-leak control.
    report = run_offline(tasks, inputs, tmp_path / "private-output")
    assert canary.encode() not in render_json(report) + render_markdown(report)
    assert b"authored_text" not in render_json(report) and b"label_payload" not in render_json(
        report
    )


def test_installed_cli_runs_from_empty_directory_and_refuses_same_output(tmp_path):
    hook = tmp_path / "sitecustomize.py"
    hook.write_text(
        "import builtins,socket\n"
        "def denied(*a,**kw):raise RuntimeError('workflow network forbidden')\n"
        "socket.create_connection=denied;socket.socket.connect=denied\n"
        "original=builtins.__import__\n"
        "def guarded(name,*a,**kw):\n"
        " if name.split('.')[0] in {'openai','typesafe','anthropic'}:\n"
        "  raise RuntimeError('SDK import forbidden')\n"
        " return original(name,*a,**kw)\n"
        "builtins.__import__=guarded\n"
    )
    env = dict(os.environ, PYTHONPATH=str(tmp_path), OPENAI_API_KEY="", TYPESAFE_API_KEY="")
    for code, reason in [
        ("import socket;socket.create_connection(('example.invalid',443))", "network forbidden"),
        ("import openai", "SDK import forbidden"),
    ]:
        control = subprocess.run(
            [sys.executable, "-B", "-c", code],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            timeout=15,
        )
        assert control.returncode != 0 and reason.encode() in control.stderr
    args = [
        sys.executable,
        "-B",
        "-m",
        "llm_router_eval",
        "eval",
        "--tasks",
        str(TASKS),
        "--vectors",
        str(VECTORS),
        "--out",
        str(tmp_path / "run"),
    ]
    first = subprocess.run(args, cwd=tmp_path, env=env, capture_output=True, timeout=45)
    assert first.returncode == 0, first.stderr
    status = json.loads(first.stdout)
    assert status["status"] == "completed_authored_controls" and status["outbound_attempts"] == 0
    before = (tmp_path / "run/report.json").read_bytes()
    second = subprocess.run(args, cwd=tmp_path, env=env, capture_output=True, timeout=15)
    assert second.returncode == 2 and json.loads(second.stdout)["status"] == "refused"
    assert (tmp_path / "run/report.json").read_bytes() == before
