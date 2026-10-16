"""Packaging checks from outside the repository, with network disabled."""

import json
import os
import subprocess
import sys


def test_installed_status_is_keyless_and_has_no_network(tmp_path):
    """what_bug_this_catches: source-cwd imports or hidden live startup calls."""
    hook = tmp_path / "sitecustomize.py"
    hook.write_text(
        "import socket\n"
        "def blocked(*args, **kwargs):\n"
        "    raise RuntimeError('Network is forbidden in scaffold status')\n"
        "socket.socket = blocked\n"
        "socket.create_connection = blocked\n",
        encoding="utf-8",
    )
    environment = {
        **os.environ,
        "PYTHONPATH": str(tmp_path),
        "OPENAI_API_KEY_FILE": str(tmp_path / "missing-openai-key"),
        "TYPESAFE_API_KEY_FILE": str(tmp_path / "missing-typesafe-key"),
        "OPENAI_API_KEY": "",
        "TYPESAFE_API_KEY": "",
    }
    control = subprocess.run(
        [sys.executable, "-c", "import socket; socket.socket()"],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert control.returncode != 0
    assert "Network is forbidden" in control.stderr
    result = subprocess.run(
        [sys.executable, "-m", "llm_router_eval", "status"],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    status = json.loads(result.stdout)
    assert status["implementation"] == "authored_offline_evaluation"
    assert status["scoring"] == "anchored_deterministic_rubric_unvalidated"
    assert status["routing"] == "seven_variants_caller_callbacks"
    assert status["data"] == "local_admission_controls_corpus_pending"
    assert status["evaluation"] == "canonical_fixed_population_authored_DEV_only"
    assert (
        status["shared_modules"] == "offline_controller_and_opt_in_native_sdk_transport_available"
    )


def test_evaluation_requires_explicit_inputs_and_output(tmp_path):
    """what_bug_this_catches: implicit data source or evaluation without an output boundary."""
    result = subprocess.run(
        [sys.executable, "-m", "llm_router_eval", "eval"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 2
    assert "required" in result.stderr and "--tasks" in result.stderr
