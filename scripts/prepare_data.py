"""Validate caller-selected local data and write a deterministic family split."""

import argparse
from pathlib import Path

from llm_router_eval.data import canonical_bytes, load_dataset, split_families
from llm_router_eval.data.corpus import DatasetAdmission
from llm_router_eval.data.splits import validate_benchmark
from llm_router_eval.shared.callledger.models import decode_wire_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--admission",
        type=Path,
        help="Trusted exact corpus admission; data cannot grant itself rights.",
    )
    parser.add_argument("--benchmark", action="store_true")
    args = parser.parse_args()
    admission = (
        None
        if args.admission is None
        else DatasetAdmission.model_validate(
            decode_wire_json(args.admission.read_text(encoding="utf-8"))
        )
    )
    if args.input.stat().st_size > 8_000_000:
        raise ValueError("dataset_bytes")
    dataset = load_dataset(args.input.read_bytes(), admission=admission)
    manifest = split_families(dataset)
    if args.benchmark:
        if admission is None:
            raise ValueError("benchmark_admission_required")
        validate_benchmark(dataset, manifest, admission=admission)
    with args.output.open("xb") as target:
        target.write(canonical_bytes(manifest.model_dump(mode="json")))
    print(
        f"Validated {len(dataset.tasks)} {dataset.evidence_status} tasks; no evaluation executed."
    )


if __name__ == "__main__":
    main()
