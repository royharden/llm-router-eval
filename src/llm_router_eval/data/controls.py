"""Ten tiny authored implementation controls, never independent benchmark Gold."""

from .corpus import Dataset, SourceProvenance, Task, dataset_bytes, digest


def make_controls() -> Dataset:
    rows = []
    for kind in ["gsm8k", "mmlu", "code", "exact", "rubric"]:
        for index in range(2):
            task_id = f"control-{kind}-{index}"
            if kind == "gsm8k":
                prompt, answer = (
                    f"There are {index + 2} blue cups and 3 red cups. How many cups?",
                    str(index + 5),
                )
            elif kind == "mmlu":
                prompt, answer = (
                    f"Control {index}: Which is a mammal? A. Cat B. Rock C. Fern D. Sand",
                    "A",
                )
            elif kind == "code":
                prompt, answer = (
                    f"Compute the sum of the integers: {index + 1}, 2, 3.",
                    str(index + 6),
                )
            elif kind == "exact":
                prompt, answer = f"Return exactly control-word-{index}.", f"control-word-{index}"
            else:
                prompt, answer = (
                    f"In two sentences, explain why control garden {index} needs water.",
                    None,
                )
            row = dict(
                task_id=task_id,
                family_id=task_id,
                kind=kind,
                prompt=prompt,
                source_id="implementation-controls",
                source_row_id=task_id,
                scoring=dict(
                    answer=answer,
                    rubric=["Mentions water supports plant growth."] if kind == "rubric" else [],
                ),
                analysis_difficulty="easy",
            )
            rows.append(dict(row, row_sha256=digest(row)))
    source = SourceProvenance(
        source_id="implementation-controls",
        source_revision="implementation-controls-v1",
        source_sha256=digest(rows),
        licence_id="MIT",
        rights_receipt_sha256=None,
        author_id="D3-implementation-author",
        role="implementation_only",
    )
    return Dataset(
        schema_version=1,
        evidence_status="synthetic_implementation",
        sources=[source],
        tasks=[Task.model_validate(row) for row in rows],
    )


def control_bytes() -> bytes:
    return dataset_bytes(make_controls())
