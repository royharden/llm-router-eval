"""Create a blank labeling template. A human owner supplies actual blinded labels later."""

import argparse
from pathlib import Path

from llm_router_eval.shared.callledger.models import decode_wire_json
from llm_router_eval.shared.routing.scoring.labels import PairSpec, blank_template


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.pairs.stat().st_size > 128000:
        raise ValueError("pair_file_bytes")
    pairs = decode_wire_json(args.pairs.read_text(encoding="utf-8"))
    if not isinstance(pairs, list) or len(pairs) > 1000:
        raise ValueError("pair_file_shape")
    payload = blank_template([PairSpec.model_validate(p) for p in pairs])
    with args.output.open("xb") as target:
        target.write(payload)
    print("Created a blank labeling template. Human labels and agreement remain pending.")


if __name__ == "__main__":
    main()
