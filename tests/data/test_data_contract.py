"""Synthetic trust doubles test admission logic; no independent Gold is created."""

import hashlib
import importlib.util
import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from llm_router_eval.data import (
    CampaignJournal,
    ConfigurationSeal,
    Dataset,
    Task,
    canonical_bytes,
    dataset_bytes,
    load_dataset,
    split_families,
)
from llm_router_eval.data.controls import control_bytes, make_controls
from llm_router_eval.data.corpus import COUNTS, DatasetAdmission, digest
from llm_router_eval.data.seals import CellRecord
from llm_router_eval.data.splits import validate_benchmark, validate_split


@pytest.fixture(autouse=True)
def regression_purpose(request):
    reasons = dict(
        [
            (
                "test_roundtrip_controls_and_family_split_are_byte_reproducible",
                "Family leakage or unstable producer.",
            ),
            (
                "test_private_gold_rubric_difficulty_mutation_cannot_change_routing_view",
                "Private scoring leaks into routing.",
            ),
            (
                "test_corrupt_ingress_fails_before_any_routing",
                "Malformed/duplicate input admitted.",
            ),
            (
                "test_duplicate_json_keys_rejected_and_validation_text_hides_inputs",
                "JSON ambiguity or raw validation leak.",
            ),
            (
                "test_infeasible_and_cross_kind_families_never_leak_across_split",
                "Stratification splits a family.",
            ),
            (
                "test_300_structural_admission_uses_explicit_trust_double_not_real_corpus",
                "Incorrect structural corpus mix accepted.",
            ),
            (
                "test_frozen_claim_needs_external_exact_source_and_independence_admission",
                "Corpus self-grants rights or independence.",
            ),
            (
                "test_controls_cannot_be_benchmark_and_split_edit_breaks_binding",
                "Controls or changed split passed as evidence.",
            ),
            (
                "test_seal_changes_and_completed_cell_overwrite_are_closed",
                "Retuning overwrites sealed completed cells.",
            ),
            (
                "test_network_blocked_producers_match_committed_tiny_assets",
                "Producer IO or fixture drift.",
            ),
            (
                "test_cli_producers_prepare_and_refuse_overwrite",
                "Producer overwrites earlier evidence.",
            ),
            (
                "test_campaign_start_binds_actual_dataset_manifest_and_seal",
                "Campaign starts with wrong IDs or seal.",
            ),
        ]
    )
    request.node.user_properties.append(
        ("what_bug_this_catches", reasons[request.node.originalname])
    )


def checked_task(data):
    data = dict(data)
    data.pop("row_sha256", None)
    return Task.model_validate(dict(data, row_sha256=digest(data)))


def seal(**changes):
    data = {key: digest([key]) for key in ConfigurationSeal.model_fields if key != "schema_version"}
    return ConfigurationSeal(**(data | changes))


def frozen_trust_double():
    """Structural 300-row UNIT double; not admitted real corpus or evaluation."""
    originals = make_controls()
    source = originals.sources[0].model_copy(
        update={
            "role": "benchmark_source",
            "rights_receipt_sha256": digest("UNIT fake rights"),
            "author_id": "unit-independent-double",
        }
    )
    tasks = []
    for kind, count in COUNTS.items():
        template = next(t for t in originals.tasks if t.kind == kind)
        for index in range(count):
            row = template.model_dump(mode="json")
            row.update(
                task_id=f"unit-{kind}-{index}",
                family_id=f"unit-{kind}-{index // 2}",
                source_row_id=f"unit-{kind}-{index}",
                prompt=f"{template.prompt} Unit variant {index}.",
            )
            tasks.append(checked_task(row))
    dataset = Dataset(
        schema_version=1, evidence_status="frozen_corpus", sources=[source], tasks=tasks
    )
    payload = dataset_bytes(dataset)
    grant = DatasetAdmission(
        dataset_sha256=hashlib.sha256(payload).hexdigest(),
        source_grants={source.source_id: source},
        freeze_receipt_sha256=digest("UNIT fake freeze"),
        independent_author_ids=[source.author_id],
        excluded_tuner_ids=["unit-tuner"],
    )
    return dataset, payload, grant


def test_roundtrip_controls_and_family_split_are_byte_reproducible():
    first = control_bytes()
    assert first == control_bytes()
    dataset = load_dataset(first)
    manifest = split_families(dataset)
    assert len(dataset.tasks) == 10 and len(manifest.dev) == len(manifest.heldout) == 5
    assert manifest == split_families(dataset)
    validate_split(dataset, manifest)
    assert not set(manifest.dev) & set(manifest.heldout)
    sides = [
        {t.family_id for t in dataset.tasks if t.task_id in ids}
        for ids in [manifest.dev, manifest.heldout]
    ]
    assert not sides[0] & sides[1]
    assert len({split_families(dataset, seed=n).model_dump_json() for n in range(8)}) > 1


def test_private_gold_rubric_difficulty_mutation_cannot_change_routing_view():
    for task in make_controls().tasks:
        row = task.model_dump(mode="json")
        row["analysis_difficulty"] = "hard"
        if task.kind == "rubric":
            row["scoring"]["rubric"] = ["PRIVATE rubric canary"]
        else:
            row["scoring"]["answer"] = "D" if task.kind == "mmlu" else "PRIVATE answer canary"
        changed = checked_task(row)
        assert changed.routing_view() == task.routing_view()
        assert set(changed.routing_view()) == {"task_id", "prompt"}
        assert "PRIVATE" not in json.dumps(changed.routing_view())


@pytest.mark.parametrize(
    "mutation",
    [
        "extra",
        "duplicate_task",
        "duplicate_prompt",
        "duplicate_row",
        "missing_source",
        "bad_hash",
        "oversize",
    ],
)
def test_corrupt_ingress_fails_before_any_routing(mutation):
    data = make_controls().model_dump(mode="json")
    if mutation == "extra":
        data["tasks"][0]["gold"] = "injected label"
    elif mutation == "duplicate_task":
        data["tasks"][1] = data["tasks"][0]
    elif mutation in {"duplicate_prompt", "duplicate_row", "missing_source", "oversize"}:
        row = data["tasks"][1]
        if mutation == "duplicate_prompt":
            row["prompt"] = data["tasks"][0]["prompt"]
        elif mutation == "duplicate_row":
            row["source_row_id"] = data["tasks"][0]["source_row_id"]
        elif mutation == "missing_source":
            row["source_id"] = "absent"
        else:
            row["prompt"] = "\u00e9" * 5000
        row.pop("row_sha256")
        row["row_sha256"] = digest(row)
    else:
        data["tasks"][0]["row_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        load_dataset(canonical_bytes(data))


def test_duplicate_json_keys_rejected_and_validation_text_hides_inputs():
    with pytest.raises(ValueError):
        load_dataset(b'{"schema_version":1,"schema_version":1}')
    row = make_controls().tasks[0].model_dump(mode="json")
    row["scoring"]["answer"] = {"PRIVATE-CANARY": "bad"}
    with pytest.raises(ValidationError) as caught:
        Task.model_validate(row)
    assert "PRIVATE-CANARY" not in str(caught.value)


def test_infeasible_and_cross_kind_families_never_leak_across_split():
    dataset = make_controls()
    data = dataset.model_dump(mode="json")
    for row in data["tasks"]:
        if row["kind"] == "gsm8k":
            row["family_id"] = "one-indivisible-math-family"
        row.update(checked_task(row).model_dump(mode="json"))
    with pytest.raises(ValueError, match="family_split_impossible"):
        split_families(Dataset.model_validate(data))
    data = dataset.model_dump(mode="json")
    data["tasks"][0]["family_id"] = data["tasks"][2]["family_id"]
    data["tasks"][0] = checked_task(data["tasks"][0]).model_dump(mode="json")
    with pytest.raises(ValueError, match="cross_kind_family"):
        split_families(Dataset.model_validate(data))


def test_300_structural_admission_uses_explicit_trust_double_not_real_corpus():
    dataset, payload, grant = frozen_trust_double()
    admitted = load_dataset(payload, admission=grant)
    manifest = split_families(admitted)
    validate_benchmark(admitted, manifest, admission=grant)
    assert len(manifest.dev) == len(manifest.heldout) == 150
    for kind, count in COUNTS.items():
        assert (
            sum(t.kind == kind and t.task_id in manifest.dev for t in dataset.tasks) == count // 2
        )
    reordered = split_families(dataset.model_copy(update={"tasks": list(reversed(dataset.tasks))}))
    assert (manifest.dev, manifest.heldout, manifest.families_sha256) == (
        reordered.dev,
        reordered.heldout,
        reordered.families_sha256,
    )
    # Exact input order changes frozen bytes; family membership stays stable.
    assert manifest.dataset_sha256 != reordered.dataset_sha256


@pytest.mark.parametrize(
    "mutation", ["no_grant", "bytes", "source", "author", "tuner", "implementation"]
)
def test_frozen_claim_needs_external_exact_source_and_independence_admission(mutation):
    dataset, payload, grant = frozen_trust_double()
    if mutation == "no_grant":
        grant = None
    elif mutation == "bytes":
        payload += b" "
    elif mutation == "source":
        grant = grant.model_copy(update={"source_grants": {}})
    elif mutation == "author":
        grant = grant.model_copy(update={"independent_author_ids": ["other"]})
    elif mutation == "tuner":
        grant = grant.model_copy(update={"excluded_tuner_ids": grant.independent_author_ids})
    else:
        data = dataset.model_dump(mode="json")
        data["sources"][0]["role"] = "implementation_only"
        payload = canonical_bytes(data)
        grant_data = grant.model_dump(mode="json")
        grant_data.update(
            dataset_sha256=hashlib.sha256(payload).hexdigest(),
            source_grants={s["source_id"]: s for s in data["sources"]},
        )
        grant = DatasetAdmission.model_validate(grant_data)
    with pytest.raises(ValueError):
        load_dataset(payload, admission=grant)


def test_controls_cannot_be_benchmark_and_split_edit_breaks_binding():
    dataset = make_controls()
    manifest = split_families(dataset)
    with pytest.raises(ValueError, match="implementation_not_benchmark"):
        validate_benchmark(dataset, manifest, admission=None)
    manifest.dev.append(manifest.heldout[0])
    with pytest.raises(ValueError, match="split_binding"):
        validate_split(dataset, manifest)


def test_seal_changes_and_completed_cell_overwrite_are_closed():
    pinned = seal()
    journal = CampaignJournal(
        seal_sha256=pinned.identity(),
        split="heldout",
        assigned_ids=["a", "b"],
        method_ids=["cheap", "frontier"],
    )
    cell = CellRecord(task_id="a", method_id="cheap", capture_sha256=digest("synthetic capture"))
    successor = journal.append(cell, seal=pinned)
    assert len(journal.missing()) == 4 and len(successor.missing()) == 3
    restored = CampaignJournal.model_validate_json(
        canonical_bytes(successor.model_dump(mode="json"))
    )
    assert restored == successor
    with pytest.raises(ValueError, match="cell_binding"):
        restored.append(cell, seal=pinned)
    with pytest.raises(ValueError, match="configuration_changed"):
        restored.append(
            cell.model_copy(update={"task_id": "b"}), seal=seal(thresholds_sha256=digest("retuned"))
        )
    for key in ConfigurationSeal.model_fields:
        if key != "schema_version":
            assert seal(**{key: digest("changed")}).identity() != pinned.identity()


def test_network_blocked_producers_match_committed_tiny_assets(monkeypatch):
    def blocked(*args, **kwargs):
        raise RuntimeError("network denied positive control")

    monkeypatch.setattr(socket, "socket", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    with pytest.raises(RuntimeError):
        socket.socket()
    root = Path(__file__).resolve().parents[2]
    assert control_bytes() == (root / "fixtures/implementation-controls/tasks.json").read_bytes()
    spec = importlib.util.spec_from_file_location(
        "fixture_producer", root / "scripts/make_fixtures.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert (
        module.fixture_bytes()
        == (root / "fixtures/implementation-controls/requests.json").read_bytes()
    )


def test_cli_producers_prepare_and_refuse_overwrite(tmp_path):
    root = Path(__file__).resolve().parents[2]
    output = tmp_path / "tasks.json"
    split = tmp_path / "split.json"
    for argv in [
        ["scripts/make_synthetic.py", "--output", str(output)],
        ["scripts/prepare_data.py", "--input", str(output), "--output", str(split)],
    ]:
        result = subprocess.run([sys.executable, *argv], cwd=root, capture_output=True, timeout=20)
        assert result.returncode == 0
    first = output.read_bytes()
    repeat = subprocess.run(
        [sys.executable, "scripts/make_synthetic.py", "--output", str(output)],
        cwd=root,
        capture_output=True,
        timeout=20,
    )
    assert repeat.returncode != 0 and output.read_bytes() == first
    assert json.loads(split.read_bytes())["seed"] == 20261014


def test_campaign_start_binds_actual_dataset_manifest_and_seal():
    dataset = make_controls()
    manifest = split_families(dataset)
    pinned = seal(
        dataset_sha256=manifest.dataset_sha256,
        split_sha256=digest(manifest.model_dump(mode="json")),
    )
    journal = CampaignJournal.start(
        dataset=dataset, manifest=manifest, seal=pinned, split="heldout", method_ids=["cheap"]
    )
    assert journal.assigned_ids == manifest.heldout and len(journal.missing()) == 5
    with pytest.raises(ValueError, match="seal_input_binding"):
        CampaignJournal.start(
            dataset=dataset, manifest=manifest, seal=seal(), split="heldout", method_ids=["cheap"]
        )
    with pytest.raises(ValueError, match="cell_binding"):
        journal.append(
            CellRecord(
                task_id=manifest.dev[0], method_id="cheap", capture_sha256=digest("synthetic")
            ),
            seal=pinned,
        )
