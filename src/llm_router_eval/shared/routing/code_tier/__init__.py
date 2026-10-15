"""A complete bounded integer grammar, never Python execution or label lookup."""

import math
import re
from dataclasses import dataclass

GRAMMAR = re.compile(
    r"Compute the (sum|product) of the integers: (-?\d{1,9}(?:, -?\d{1,9}){0,19})\.", re.ASCII
)


@dataclass(frozen=True)
class CodeAnswer:
    text: str
    handler: str


def solve(text: str) -> CodeAnswer | None:
    if not isinstance(text, str) or len(text.encode("utf-8")) > 512:
        return None
    match = GRAMMAR.fullmatch(text)
    if match is None:
        return None
    values = [int(value) for value in match[2].split(", ")]
    result = sum(values) if match[1] == "sum" else math.prod(values)
    if abs(result) > 10**18:
        return None
    return CodeAnswer(text=f"FINAL: {result}", handler=f"integer_{match[1]}_v1")


def can_handle(text: str) -> bool:
    return solve(text) is not None
