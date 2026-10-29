"""Original UNIT structural trust doubles, never real corpus/captures/labels or live proof.

The contract-shaped origin="live" records below are fictional UNIT input objects.
They exercise recorded-input validation; no fixture is claimed to be an actual recording.
"""

import base64
import builtins
import hashlib
import json
import socket
import sqlite3
from dataclasses import asdict

import pytest

from llm_router_eval.cli import main
from llm_router_eval.data.controls import make_controls
from llm_router_eval.data.corpus import (
    COUNTS,
    Dataset,
    DatasetAdmission,
    Task,
    dataset_bytes,
    digest,
    load_dataset,
)
from llm_router_eval.data.seals import ConfigurationSeal
from llm_router_eval.data.splits import split_families, validate_benchmark
from llm_router_eval.evaluation.campaign import (
    CampaignBinding,
    CampaignGrant,
    capture_book_digest,
    database_location_sha256,
)
from llm_router_eval.evaluation.control_book import build_control_book
from llm_router_eval.evaluation.domain import ReferenceAdmission, prepare_view
from llm_router_eval.evaluation.engine import policy_configuration_sha
from llm_router_eval.evaluation.replay_workflow import (
    NAMES,
    ReplayAdmission,
    ReplayInputs,
    expected_seal,
    run_recorded_replay,
)
from llm_router_eval.evaluation.workflow import offline_authority
from llm_router_eval.shared.callledger.models import canonical_digest as h
from llm_router_eval.shared.evalkit.runner import HeldoutAdmission
from llm_router_eval.shared.providers.captures import LiveCaptureReceipt
from llm_router_eval.shared.providers.models import SourceCaptureRef
from llm_router_eval.shared.routing.context import RouteInput
from llm_router_eval.shared.routing.policies import Policy, PolicyConfig


def encoded(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False) + "\n").encode()


@pytest.fixture(autouse=True)
def no_effects(monkeypatch, request):
    def denied(*args, **kwargs):
        raise AssertionError("UNIT replay attempted network")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    with pytest.raises(AssertionError, match="attempted network"):
        socket.create_connection(("example.invalid", 443))
    original = builtins.__import__

    def no_sdk(name, *args, **kwargs):
        if name.split(".")[0] in {"openai", "typesafe_sdk"}:
            raise AssertionError("UNIT replay imported an SDK")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_sdk)
    request.node.user_properties.append(
        (
            "what_bug_this_catches",
            "Replay must retain separate externally pinned authority, strict recorded origin, "
            "zero dispatch, fixed denominators and safe outputs before any quality claim.",
        )
    )


def structural_case(tmp_path, *, split="dev", altered_labels=False, dataset_encoding="canonical"):
    """300 fictional UNIT rows, two available source items; all labels agent_provisional."""
    originals = make_controls()
    source = originals.sources[0].model_copy(
        update={
            "source_id": "UNIT_PRIVATE_SOURCE_SENTINEL",
            "role": "benchmark_source",
            "rights_receipt_sha256": h("UNIT fictional rights, not legal authority"),
            "author_id": "UNIT-independent-author-double",
        }
    )
    tasks = []
    for kind, count in COUNTS.items():
        template = next(t for t in originals.tasks if t.kind == kind)
        for i in range(count):
            row = template.model_dump(mode="json", exclude={"row_sha256"})
            row.update(
                task_id=f"UNIT-{kind}-{i}",
                family_id=f"UNIT-{kind}-{i // 2}",
                source_id=source.source_id,
                source_row_id=f"UNIT-{kind}-{i}",
                prompt=f"{template.prompt} UNIT_PUBLIC_PROMPT_SENTINEL {i}.",
            )
            if kind == "rubric":
                row["scoring"]["rubric"].append("UNIT_PRIVATE_RUBRIC_SENTINEL")
            if altered_labels and kind != "rubric":
                row["scoring"]["answer"] = "D" if kind == "mmlu" else "999"
            tasks.append(Task.model_validate(row | {"row_sha256": digest(row)}))
    dataset = Dataset(
        schema_version=1, evidence_status="frozen_corpus", sources=[source], tasks=tasks
    )
    payload = dataset_bytes(dataset)
    if dataset_encoding == "pretty":
        payload = (json.dumps(json.loads(payload), ensure_ascii=False, indent=2) + "\n").encode()
    elif dataset_encoding == "trailing":
        payload += b" \n\t"
    elif dataset_encoding != "canonical":
        raise ValueError("UNIT dataset encoding")
    dataset_grant = DatasetAdmission(
        dataset_sha256=hashlib.sha256(payload).hexdigest(),
        source_grants={source.source_id: source},
        freeze_receipt_sha256=h("UNIT fake freeze"),
        independent_author_ids=[source.author_id],
        excluded_tuner_ids=["UNIT-tuner"],
    )
    reference = ReferenceAdmission(
        receipt_sha256=h("UNIT fake reference receipt"),
        dataset_sha256=dataset_grant.dataset_sha256,
        label_sources={t.task_id: "agent_provisional" for t in tasks},
        provenance_refs={t.task_id: "UNIT-not-human" for t in tasks},
        independent_seal_sha256=h("UNIT independent seal double"),
        dev_config_sha256=h("UNIT dev lock double"),
    )
    trusted_references = {reference.receipt_sha256: h(reference.model_dump(mode="json"))}
    partition = split_families(dataset)
    assigned = getattr(partition, split)
    selected = [t for t in tasks if t.task_id in assigned and t.kind != "rubric"][:2]
    vectors = encoded(
        dict(
            evidence_status="synthetic_implementation",
            schema_version=1,
            kind="authored_request_vectors",
            fixtures=[
                dict(
                    task_id=t.task_id,
                    request={
                        "messages": [
                            {"role": "user", "content": [{"type": "text", "text": t.prompt}]}
                        ]
                    },
                    authored_text="1",
                )
                for t in selected
            ],
        )
    )
    authority, limits = offline_authority()
    book = build_control_book(
        [RouteInput.from_data_view(t.routing_view()) for t in selected],
        vectors_payload=vectors,
        authority=authority,
        limits=limits,
        run_id="UNIT-structural-replay-not-live",
    )
    captures = []
    live_receipts = {}
    # Original contract doubles: field values are explicitly fictional. No real recording
    # or independent provenance is asserted by these test-owned receipt objects.
    for i, fixture in enumerate(book.records):
        declared = SourceCaptureRef(
            capture_id=f"UNIT-fictional-capture-{i}",
            capture_sha256=hashlib.sha256(fixture.content_json).hexdigest(),
            origin="live",
            source_run_id="UNIT-NO-PROVIDER-OR-REAL-RECORDING",
            request_identity_sha256=fixture.source.request_identity_sha256,
            model_requested=fixture.source.model_requested,
            model_reported=fixture.source.model_reported,
            first_output_kind="unavailable",
        )
        receipt = LiveCaptureReceipt(
            receipt_sha256=h(["UNIT fictional capture receipt", i]),
            capture_sha256=declared.capture_sha256,
            source_ref_sha256=h(declared.model_dump(mode="json")),
            namespace=fixture.namespace,
        )
        live_receipts[receipt.receipt_sha256] = receipt
        captures.append(
            dict(
                content_base64=base64.b64encode(fixture.content_json).decode(),
                source=declared.model_dump(mode="json"),
                namespace=fixture.namespace,
                source_generation=fixture.source_generation,
                consumer_context_sha256=fixture.consumer_context_sha256,
                dispatched_envelope_sha256=fixture.dispatched_envelope_sha256,
                review_receipt_sha256=receipt.receipt_sha256,
            )
        )
    config = book.specs[0].config
    policy_configs = {name: PolicyConfig() for name in NAMES}
    inputs = ReplayInputs(
        schema_version=1,
        run_id="UNIT-structural-replay-not-live",
        frozen_at="2026-10-14T00:00:00Z",
        config=config,
        authority=authority,
        limits=limits,
        policy_configs=policy_configs,
        items=[dict(public_input=s.item, cheap_text=s.expected_cheap_text) for s in book.specs],
        captures=captures,
    )
    bundle = encoded(inputs.model_dump(mode="json"))
    policies = [Policy(name, config=policy_configs[name]) for name in NAMES]
    configs = {p.name: policy_configuration_sha(p, config.sha256) for p in policies}
    view = prepare_view(
        payload,
        frozen_at=inputs.frozen_at,
        method_configs=configs,
        origin="recorded_replay",
        dataset_admission=dataset_grant,
        reference_admission=reference,
        trusted_reference_receipts=trusted_references,
    )
    records = tuple(c.record() for c in inputs.captures)
    seal = expected_seal(
        payload, partition, inputs, records, configs, hashlib.sha256(bundle).hexdigest()
    )
    target = tmp_path / "UNIT-output"
    binding = CampaignBinding(
        state_id="UNIT-state",
        campaign_id="UNIT-campaign",
        split=split,
        evidence_status="frozen_corpus",
        mode="strict_replay",
        seal_sha256=seal.identity(),
        source_book_sha256=capture_book_digest(records),
        public_config_sha256=config.sha256,
        assigned_ids=[r.item_id for r in view.split_records(split)],
        method_config_sha256=configs,
        manifest_sha256=view.manifest_sha256,
        independent_seal_sha256=reference.independent_seal_sha256,
        dev_config_sha256=reference.dev_config_sha256,
        history_ref=view.manifest.run_history_ref,
        source_revision=h("UNIT fictional source revision"),
    )
    grant = CampaignGrant(
        grant_sha256=h("UNIT fictional campaign grant"),
        binding_sha256=binding.sha256,
        state_id=binding.state_id,
        database_location_sha256=database_location_sha256(target / "campaign.sqlite3"),
        heldout_allowed=split == "heldout",
    )
    exposure = HeldoutAdmission(
        view.manifest_sha256,
        reference.independent_seal_sha256,
        reference.dev_config_sha256,
        configs,
        binding.source_revision,
        binding.history_ref,
        None,
    )
    admission = ReplayAdmission(
        schema_version=1,
        input_bundle_sha256=hashlib.sha256(bundle).hexdigest(),
        dataset_admission=dataset_grant,
        reference_admission=reference,
        trusted_reference_receipts=trusted_references,
        live_capture_receipts=live_receipts,
        seal=seal,
        campaign_binding=binding,
        campaign_grant=grant,
        trusted_campaign_grants={grant.grant_sha256: h(grant.model_dump(mode="json"))},
        heldout_admission=asdict(exposure) if split == "heldout" else None,
    ).model_dump(mode="json")
    paths = [
        tmp_path / name for name in ("UNIT-data.json", "UNIT-bundle.json", "UNIT-admission.json")
    ]
    paths[0].write_bytes(payload)
    paths[1].write_bytes(bundle)
    return paths, admission, target


def invoke(paths, admission, target, *, split="dev", expected=None):
    wire = encoded(admission)
    paths[2].write_bytes(wire)
    return run_recorded_replay(
        *paths, expected or hashlib.sha256(wire).hexdigest(), target, split=split
    )


def test_actual_replay_ports_zero_dispatch_partial_denominators_and_safe_reports(tmp_path):
    paths, admission, target = structural_case(tmp_path)
    report = invoke(paths, admission, target)
    assert report["evidence_origin"] == "recorded_replay"
    assert report["corpus_N"] == 150 and len(report["metrics"]) == 7
    assert all(row["corpus_N"] == 150 for row in report["metrics"])
    assert report["collection"] == dict(
        status="incomplete", expected_cells=900, retained_cells=12, uncollected_cells=888
    )
    assert report["collection_cost"]["source_point_usd"] is None
    assert report["ledger"]["dispatched_attempts"] == 0
    assert report["ledger"]["known_spend_usd"] == "0"
    with sqlite3.connect(target / "campaign.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM cells").fetchone()[0] == 12
    with sqlite3.connect(target / "accounting.sqlite3") as db:
        assert (
            db.execute(
                "SELECT COUNT(*) FROM events WHERE json_extract(payload,'$.kind')='reuse_accounted'"
            ).fetchone()[0]
            == 12
        )
    public = (target / "report.json").read_text() + (target / "report.md").read_text()
    assert "UNIT_PUBLIC_PROMPT_SENTINEL" not in public
    assert "UNIT_PRIVATE_SOURCE_SENTINEL" not in public
    assert "UNIT_PRIVATE_RUBRIC_SENTINEL" not in public
    assert "# Admitted recorded routing replay" in public
    assert "Authored source costs are synthetic" not in public


@pytest.mark.parametrize(
    "mutation",
    ["expected_hash", "bundle", "dataset", "reference", "capture", "seal", "grant", "history"],
)
def test_tampered_independent_boundaries_refuse_before_output(tmp_path, mutation):
    paths, admission, target = structural_case(tmp_path)
    expected = None
    if mutation == "expected_hash":
        expected = "0" * 64
    elif mutation == "bundle":
        paths[1].write_bytes(paths[1].read_bytes() + b" ")
    elif mutation == "dataset":
        paths[0].write_bytes(paths[0].read_bytes() + b" ")
    elif mutation == "reference":
        admission["trusted_reference_receipts"] = {}
    elif mutation == "capture":
        admission["live_capture_receipts"] = {}
    elif mutation == "seal":
        admission["seal"]["prices_sha256"] = "0" * 64
    elif mutation == "grant":
        admission["trusted_campaign_grants"] = {}
    else:
        admission["campaign_binding"]["history_ref"] = "UNIT-wrong-history"
    with pytest.raises(ValueError):
        invoke(paths, admission, target, expected=expected)
    assert not target.exists()


@pytest.mark.parametrize("dataset_encoding", ["pretty", "trailing"])
def test_admitted_json_representation_keeps_raw_authority(tmp_path, dataset_encoding):
    paths, admission, target = structural_case(tmp_path, dataset_encoding=dataset_encoding)
    report = invoke(paths, admission, target)
    raw_sha256 = hashlib.sha256(paths[0].read_bytes()).hexdigest()
    assert report["input_dataset_sha256"] == raw_sha256
    assert admission["dataset_admission"]["dataset_sha256"] == raw_sha256
    assert report["corpus_N"] == 150 and report["ledger"]["dispatched_attempts"] == 0


def test_benchmark_raw_payload_cannot_authorize_a_different_dataset_object(tmp_path):
    paths, admission, _ = structural_case(tmp_path)
    payload = paths[0].read_bytes()
    grant = DatasetAdmission.model_validate(admission["dataset_admission"])
    dataset = load_dataset(payload, admission=grant)
    reordered = dataset.model_copy(update={"tasks": list(reversed(dataset.tasks))})
    with pytest.raises(ValueError, match="benchmark_dataset_binding"):
        validate_benchmark(
            reordered,
            split_families(reordered),
            admission=grant,
            source_payload=payload,
        )


def test_existing_output_is_preserved_byte_for_byte(tmp_path):
    paths, admission, target = structural_case(tmp_path)
    target.mkdir()
    (target / "sentinel").write_bytes(b"UNIT KEEP ORIGINAL")
    with pytest.raises(ValueError, match="output_exists"):
        invoke(paths, admission, target)
    assert list(target.iterdir()) == [target / "sentinel"]
    assert (target / "sentinel").read_bytes() == b"UNIT KEEP ORIGINAL"


@pytest.mark.parametrize("missing", ["heldout_admission", "independent_seal_sha256"])
def test_heldout_needs_actual_separate_matching_authority_before_output(tmp_path, missing):
    paths, admission, target = structural_case(tmp_path, split="heldout")
    if missing == "heldout_admission":
        admission[missing] = None
    else:
        admission["campaign_binding"][missing] = None
    with pytest.raises(ValueError):
        invoke(paths, admission, target, split="heldout")
    assert not target.exists()


def test_canonical_heldout_exposure_once_only_using_unit_structural_authority(tmp_path):
    paths, admission, target = structural_case(tmp_path, split="heldout")
    report = invoke(paths, admission, target, split="heldout")
    assert report["split"] == "heldout" and report["ledger"]["dispatched_attempts"] == 0
    with sqlite3.connect(target / "campaign.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM exposure").fetchone()[0] == 1
    original = (target / "report.json").read_bytes()
    with pytest.raises(ValueError, match="output_exists"):
        invoke(paths, admission, target, split="heldout")
    assert original == (target / "report.json").read_bytes()


def test_cli_closed_refusal_never_prints_private_validation_input(tmp_path, capsys):
    paths, admission, target = structural_case(tmp_path)
    admission["PRIVATE_UNIT_GOLD_SENTINEL"] = "PRIVATE_UNIT_ANSWER_SENTINEL"
    data = encoded(admission)
    paths[2].write_bytes(data)
    assert (
        main(
            [
                "replay",
                "--dataset",
                str(paths[0]),
                "--replay",
                str(paths[1]),
                "--admission",
                str(paths[2]),
                "--admission-sha256",
                hashlib.sha256(data).hexdigest(),
                "--out",
                str(target),
            ]
        )
        == 2
    )
    output = capsys.readouterr()
    assert json.loads(output.out)["status"] == "refused"
    assert "SENTINEL" not in output.out + output.err
    assert not target.exists()


def update_bundle_pin(paths, admission, bundle, *, rebind_seal=False):
    """Test-owned structural authority; not a CLI feature or real provenance receipt."""
    data = encoded(bundle)
    paths[1].write_bytes(data)
    admission["input_bundle_sha256"] = hashlib.sha256(data).hexdigest()
    if rebind_seal:
        admission["seal"]["input_manifest_sha256"] = admission["input_bundle_sha256"]
        admission["campaign_binding"]["seal_sha256"] = ConfigurationSeal.model_validate(
            admission["seal"]
        ).identity()
        binding = CampaignBinding.model_validate(admission["campaign_binding"])
        admission["campaign_grant"]["binding_sha256"] = binding.sha256
        grant = CampaignGrant.model_validate(admission["campaign_grant"])
        admission["trusted_campaign_grants"] = {
            grant.grant_sha256: h(grant.model_dump(mode="json"))
        }


@pytest.mark.parametrize("mutation", ["synthetic", "outbound", "duplicate", "prompt", "private"])
def test_actual_origin_quota_and_public_scope_guards_with_matching_bundle_pin(tmp_path, mutation):
    paths, admission, target = structural_case(tmp_path)
    bundle = json.loads(paths[1].read_bytes())
    if mutation == "synthetic":
        bundle["captures"][0]["source"]["origin"] = "synthetic"
        reason = "recorded_capture_required"
    elif mutation == "outbound":
        bundle["authority"]["max_outbound_attempts"] = 1
        reason = "replay_zero_dispatch_configuration"
    elif mutation == "duplicate":
        bundle["items"][1] = bundle["items"][0]
        reason = "replay_public_input_binding"
    elif mutation == "prompt":
        bundle["items"][0]["public_input"]["text"] = "UNIT public prompt replaced"
        reason = "replay_public_input_binding"
    else:
        bundle["items"][0]["public_input"]["rubric"] = "UNIT_PRIVATE_RUBRIC_SENTINEL"
        reason = "extra_forbidden"
    update_bundle_pin(paths, admission, bundle, rebind_seal=mutation in {"duplicate", "prompt"})
    with pytest.raises(ValueError, match=reason):
        invoke(paths, admission, target)
    assert not target.exists()


def test_successful_cli_reports_only_actual_aggregate_facts(tmp_path, capsys):
    paths, admission, target = structural_case(tmp_path)
    wire = encoded(admission)
    paths[2].write_bytes(wire)
    assert (
        main(
            [
                "replay",
                "--dataset",
                str(paths[0]),
                "--replay",
                str(paths[1]),
                "--admission",
                str(paths[2]),
                "--admission-sha256",
                hashlib.sha256(wire).hexdigest(),
                "--out",
                str(target),
            ]
        )
        == 0
    )
    output = capsys.readouterr()
    actual = json.loads(output.out)
    assert actual["status"] == "completed_recorded_replay"
    assert actual["corpus_N"] == 150 and actual["collection_status"] == "incomplete"
    assert actual["outbound_attempts"] == 0 and actual["current_external_cost_usd"] == "0"
    assert "SENTINEL" not in output.out + output.err


def test_deeply_nested_invalid_replay_input_is_a_safe_cli_refusal(tmp_path, capsys):
    bad = tmp_path / "UNIT-deep.json"
    data = ("[" * 3000 + "0" + "]" * 3000).encode()
    bad.write_bytes(data)
    target = tmp_path / "UNIT-output"
    assert (
        main(
            [
                "replay",
                "--dataset",
                str(bad),
                "--replay",
                str(bad),
                "--admission",
                str(bad),
                "--admission-sha256",
                hashlib.sha256(data).hexdigest(),
                "--out",
                str(target),
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().out)["status"] == "refused"
    assert not target.exists()


def test_changed_private_labels_cannot_change_replay_public_inputs_or_capture_requests(
    tmp_path,
):
    first = tmp_path / "UNIT-first"
    second = tmp_path / "UNIT-second"
    first.mkdir()
    second.mkdir()
    paths_a, admission_a, target_a = structural_case(first)
    paths_b, admission_b, target_b = structural_case(second, altered_labels=True)
    assert paths_a[0].read_bytes() != paths_b[0].read_bytes()
    # The bundle includes every actual prepared public item and capture/request identity,
    # but no private labels. Reissued test authority must not change those request bytes.
    assert paths_a[1].read_bytes() == paths_b[1].read_bytes()
    report_a = invoke(paths_a, admission_a, target_a)
    report_b = invoke(paths_b, admission_b, target_b)
    assert report_a["source_book_sha256"] == report_b["source_book_sha256"]
    assert report_a["manifest_sha256"] != report_b["manifest_sha256"]
    assert (
        report_a["ledger"]["dispatched_attempts"] == report_b["ledger"]["dispatched_attempts"] == 0
    )
