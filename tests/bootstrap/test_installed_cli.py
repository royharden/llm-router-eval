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
    assert status["implementation"] == "ledger_and_pricing_foundation"
    assert status["evaluation"] == "not_implemented"
    assert status["shared_modules"] == "ledger_pricing_available_provider_execution_not_implemented"


def test_unimplemented_evaluation_command_is_rejected(tmp_path):
    """what_bug_this_catches: advertising a benchmark that did not execute."""
    result = subprocess.run(
        [sys.executable, "-m", "llm_router_eval", "eval"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 2
    assert "invalid choice" in result.stderr
