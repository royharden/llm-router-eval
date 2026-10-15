"""Produce authored implementation request vectors, not recorded provider captures."""

import argparse
from pathlib import Path

from llm_router_eval.data import canonical_bytes
from llm_router_eval.data.controls import make_controls
from llm_router_eval.shared.providers.models import GenerateRequest


def fixture_bytes():
    fixtures = []
    for task in make_controls().tasks:
        request = GenerateRequest.model_validate(
            dict(
                schema_version=1,
                kind="generate",
                model="fixture-model",
                messages=[dict(role="user", content=[dict(type="text", text=task.prompt)])],
                schema=None,
                max_output_tokens=64,
                stream=False,
                reasoning_effort=None,
                temperature=None,
            )
        )
        fixtures.append(
            dict(
                task_id=task.task_id,
                request=request.model_dump(mode="json", by_alias=True),
                authored_text=task.scoring.answer
                or "Water supports plant growth. It keeps plants healthy.",
            )
        )
    return canonical_bytes(
        dict(
            schema_version=1,
            evidence_status="synthetic_implementation",
            kind="authored_request_vectors",
            fixtures=fixtures,
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    with args.output.open("xb") as target:
        target.write(fixture_bytes())
    print("Wrote authored request vectors; not Provider captures or live observations.")


if __name__ == "__main__":
    main()
