"""Keyless status entrypoint; evaluation modules are not implemented yet."""

import argparse
import json
from collections.abc import Sequence

from . import __version__


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="llm-router-eval",
        description=(
            "Local router evaluation. Current implementation: offline provider foundation."
        ),
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status", help="Show implemented capabilities without network or keys.")
    parser.parse_args(argv)
    print(
        json.dumps(
            {
                "application": "llm-router-eval",
                "version": __version__,
                "implementation": "offline_provider_foundation",
                "evaluation": "not_implemented",
                "shared_modules": "offline_controller_available_live_transport_not_implemented",
                "network": "not_required",
            },
            sort_keys=True,
        )
    )
    return 0
