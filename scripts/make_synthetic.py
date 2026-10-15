"""Write ten original implementation controls; never create benchmark/heldout Gold."""

import argparse
from pathlib import Path

from llm_router_eval.data.controls import control_bytes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    with args.output.open("xb") as target:
        target.write(control_bytes())
    print("Wrote 10 synthetic implementation controls; not a benchmark corpus.")


if __name__ == "__main__":
    main()
