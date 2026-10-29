"""Keyless status, authored controls and separately admitted recorded replay."""

import argparse
import json
import sqlite3
from collections.abc import Sequence

from . import __version__


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="llm-router-eval",
        description=(
            "Local authored offline routing controls. No live providers or benchmark claims."
        ),
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status", help="Show implemented capabilities without network or keys.")
    replay = subparsers.add_parser(
        "replay", help="Compare separately admitted corpus and recorded captures without dispatch."
    )
    replay.add_argument("--dataset", required=True, help="Explicit admitted corpus JSON path.")
    replay.add_argument("--replay", required=True, help="Closed recorded-capture bundle JSON path.")
    replay.add_argument(
        "--admission", required=True, help="Separate trusted caller admission JSON."
    )
    replay.add_argument(
        "--admission-sha256",
        required=True,
        help="Expected admission digest from the trusted caller.",
    )
    replay.add_argument("--split", choices=["dev", "heldout"], default="dev")
    replay.add_argument("--out", required=True, help="New output directory; parent must exist.")
    for name in ["eval", "demo"]:
        command = subparsers.add_parser(
            name, help="Run small authored DEV controls with zero outbound quota."
        )
        command.add_argument("--tasks", required=True, help="Explicit synthetic tasks JSON path.")
        command.add_argument(
            "--vectors", required=True, help="Explicit authored request-vectors JSON path."
        )
        command.add_argument(
            "--out", required=True, help="New output directory; parent must exist."
        )
        command.add_argument(
            "--plots",
            action="store_true",
            help="Export PNG/SVG figures and canonical family sampling intervals.",
        )
        if name == "demo":
            command.add_argument(
                "--scenario",
                choices=[
                    "normal",
                    "code",
                    "cheap",
                    "escalation",
                    "malformed",
                    "budget",
                    "unknown_cost",
                ],
                default="normal",
            )
    args = parser.parse_args(argv)
    if args.command == "replay":
        from .evaluation.replay_workflow import run_recorded_replay

        try:
            report = run_recorded_replay(
                args.dataset,
                args.replay,
                args.admission,
                args.admission_sha256,
                args.out,
                split=args.split,
            )
        except (OSError, ValueError, sqlite3.Error, RecursionError):
            print(json.dumps({"status": "refused", "reason": "invalid_replay_or_existing_output"}))
            return 2
        print(
            json.dumps(
                dict(
                    status="completed_recorded_replay",
                    split=report["split"],
                    corpus_N=report["corpus_N"],
                    collection_status=report["collection"]["status"],
                    outbound_attempts=report["ledger"]["dispatched_attempts"],
                    current_external_cost_usd=report["ledger"]["known_spend_usd"],
                ),
                sort_keys=True,
            )
        )
        return 0
    if args.command in {"eval", "demo"}:
        from .evaluation.workflow import run_offline

        try:
            report = run_offline(
                args.tasks,
                args.vectors,
                args.out,
                scenario=getattr(args, "scenario", "normal"),
                plots=args.plots,
            )
        except (OSError, ValueError):
            # Input/provider validation bodies may contain private source text; omit them.
            print(
                json.dumps(
                    {"status": "refused", "reason": "invalid_input_or_existing_output"},
                    sort_keys=True,
                )
            )
            return 2
        print(
            json.dumps(
                dict(
                    status="completed_authored_controls",
                    scenario=report["scenario"],
                    corpus_N=report["corpus_N"],
                    deterministic_eligible_N=report["deterministic_eligible_N"],
                    rubric_unscored_N=report["rubric_unscored_N"],
                    outbound_attempts=report["ledger"]["dispatched_attempts"],
                    current_external_cost_usd=report["ledger"]["known_spend_usd"],
                    collection_status=report["collection"]["status"],
                    invalid_source_outputs=report["source_status_counts"].get("invalid_output", 0),
                    virtual_budget_rejected=report.get("virtual_budget_control", {}).get("status")
                    == "budget_rejected",
                ),
                sort_keys=True,
            )
        )
        return 0
    print(
        json.dumps(
            {
                "application": "llm-router-eval",
                "version": __version__,
                "implementation": "authored_offline_evaluation",
                "scoring": "anchored_deterministic_rubric_unvalidated",
                "routing": "seven_variants_caller_callbacks",
                "data": "local_admission_controls_corpus_pending",
                "evaluation": "canonical_fixed_population_authored_DEV_only",
                "recorded_replay": "separate_caller_admitted_zero_dispatch_command",
                "shared_modules": "offline_controller_and_opt_in_native_sdk_transport_available",
                "network": "not_required",
            },
            sort_keys=True,
        )
    )
    return 0
