"""Regression controls for private-label leaks, population loss and invented human evidence."""

import hashlib
import json
import socket
from pathlib import Path

import pytest

from llm_router_eval.data.controls import make_controls
from llm_router_eval.data.corpus import (
    COUNTS,
    Dataset,
    DatasetAdmission,
    Task,
    dataset_bytes,
    digest,
)
from llm_router_eval.evaluation import engine
from llm_router_eval.evaluation.control_book import build_control_book
from llm_router_eval.evaluation.domain import AnswerAdapter, ReferenceAdmission, prepare_view
from llm_router_eval.evaluation.engine import MatrixSource, policy_configuration_sha, run_comparison
from llm_router_eval.shared.callledger import Ledger
from llm_router_eval.shared.callledger.models import BudgetAuthority, Limits, canonical_digest
from llm_router_eval.shared.evalkit import runner as canonical_runner
from llm_router_eval.shared.evalkit.corpus import FrozenCorpusError
from llm_router_eval.shared.routing.context import RouteInput
from llm_router_eval.shared.routing.policies import Policy

ROOT = Path(__file__).resolve().parents[2]
NAMES = (
    "always_cheap",
    "always_frontier",
    "rules",
    "classifier",
    "jev_gate",
    "cascade_jev",
    "cascade_llm",
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*a, **kw):
        raise RuntimeError("domain controls forbid network")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


def fixture_bundle(tmp_path, *, tasks=None, omit=(), stale=()):
    data = make_controls()
    authority = BudgetAuthority(
        schema_version=1,
        authority_id="domain-control-authority",
        scope_sha256=digest("SYNTHETIC DEV ONLY"),
        cap_usd="0",
        max_outbound_attempts=0,
        run_deadline_utc="2090-01-01T00:00:00Z",
    )
    limits = Limits(
        schema_version=1,
        max_attempts_total=2,
        max_content_repairs=0,
        operation_deadline_s=60.0,
        attempt_timeout_s=20.0,
        run_deadline_utc=authority.run_deadline_utc,
        max_outbound_attempts=0,
        max_concurrent_operations=2,
        max_total_input_tokens=100_000,
        max_state_plus_longest_question_tokens=100_000,
        max_request_bytes=128_000,
        max_response_bytes=128_000,
        max_questions=32,
        max_choice_options=64,
        max_total_output_tokens=512,
    )
    items = [RouteInput.from_data_view(t.routing_view()) for t in (tasks or data.tasks)]
    book = build_control_book(
        items,
        vectors_payload=(ROOT / "fixtures/implementation-controls/requests.json").read_bytes(),
        authority=authority,
        limits=limits,
        run_id="UNIT-domain",
    )
    ledger = Ledger(
        tmp_path / "domain.sqlite3", authority=authority, trusted_requests=book.request_evidence
    )
    specs = tuple(s for s in book.specs if s.item.prompt_id not in omit)
    source = MatrixSource(specs=specs, records=book.records, ledger=ledger, budget=authority)
    policies = tuple(Policy(n) for n in NAMES)
    configs = {p.name: policy_configuration_sha(p, book.specs[0].config.sha256) for p in policies}
    view = prepare_view(
        dataset_bytes(data),
        frozen_at="2026-10-14T00:00:00Z",
        method_configs=configs,
        all_synthetic_dev=True,
    )
    if stale:
        original = source.lookup

        def lookup(gold):
            value = original(gold)
            return (
                canonical_runner.SourceView("available", "f" * 64, value.public_payload)
                if gold.item_id in stale
                else value
            )

        source.lookup = lookup
    return view, source, policies, ledger, book, configs


def test_actual_canonical_seven_policy_population_and_no_label_input(tmp_path, monkeypatch):
    assert engine.compare is canonical_runner.compare
    view, source, policies, ledger, book, _ = fixture_bundle(tmp_path)
    seen = []
    original = source.matrix_for

    def checked(public):
        seen.append(public)
        assert set(public.public_payload) == {"task_id", "prompt"}
        return original(public)

    monkeypatch.setattr(source, "matrix_for", checked)
    with ledger:
        result = run_comparison(view, source, policies).comparison
        assert result.corpus_count == 10 and len(seen) == 70
        assert set(result.methods) == set(NAMES)
        for method in result.methods.values():
            assert method.status == "executed" and method.calls_observed is None
            assert method.requested_item_ids == result.frozen_item_ids
            assert len(method.actual_predictions) == len(method.outcomes) == 10
            assert sum(o.eligibility == "unlabelled" for o in method.outcomes) == 2
            assert all(o.correct is None for o in method.outcomes if o.eligibility == "unlabelled")
            assert all(
                p.origin == "synthetic" and p.human_effective_output is None
                for p in method.actual_predictions
            )
        assert len(source._matrices) == 10
        assert ledger.totals()["dispatched_attempts"] == 0
        assert (
            ledger.connection.execute(
                "SELECT COUNT(*) FROM events WHERE json_extract(payload,'$.kind')='reuse_accounted'"
            ).fetchone()[0]
            == 60
        )
        assert len(source.executions) == 70
        assert all(r.label_source == "synthetic" for r in view.records)
        assert all(r.label_provenance_ref is None for r in view.records)
        assert all(
            p.model is None
            for m in result.methods.values()
            for p in m.actual_predictions
            if p.domain_payload["selected_tier"] == "code"
        )
        assert book.specs[0].root_context.purpose == "matrix.collect"


def test_missing_stale_disabled_keep_fixed_ids_without_fabricated_predictions(tmp_path):
    data = make_controls()
    missing, stale = data.tasks[0].task_id, data.tasks[1].task_id
    view, source, policies, ledger, _, _ = fixture_bundle(tmp_path, omit=(missing,), stale=(stale,))
    with ledger:
        result = run_comparison(
            view, source, policies, disabled_methods={"always_frontier"}
        ).comparison
        disabled = result.methods["always_frontier"]
        assert disabled.status == "not_run" and disabled.calls_observed == 0
        assert disabled.actual_predictions == disabled.outcomes == ()
        assert disabled.requested_item_ids == result.frozen_item_ids
        for name, method in result.methods.items():
            if name == "always_frontier":
                continue
            rows = {o.item_id: o for o in method.outcomes}
            assert len(rows) == 10 and len(method.actual_predictions) == 8
            assert rows[missing].status == "unavailable" and rows[missing].correct is False
            assert rows[stale].status == "stale" and rows[stale].correct is False
            assert not rows[missing].ledger_refs and not rows[stale].ledger_refs
        assert len(source._matrices) == 8 and len(source.executions) == 48


def test_label_mutation_changes_scores_but_no_source_keys_or_requests(tmp_path):
    view, source, policies, ledger, book, configs = fixture_bundle(tmp_path)
    changed = make_controls().model_dump(mode="json")
    row = changed["tasks"][0]
    row["scoring"]["answer"] = "999"
    row["analysis_difficulty"] = "hard"
    row["row_sha256"] = digest({k: v for k, v in row.items() if k != "row_sha256"})
    altered = prepare_view(
        dataset_bytes(Dataset.model_validate(changed)),
        frozen_at="2026-10-14T00:00:00Z",
        method_configs=configs,
        all_synthetic_dev=True,
    )
    keys = [c.prepared.request.identity_payload() for s in book.specs for c in s.cells]
    with ledger:
        first = run_comparison(view, source, policies).comparison
        second = run_comparison(altered, source, policies).comparison
        target = row["task_id"]
        assert (
            next(o for o in first.methods["always_cheap"].outcomes if o.item_id == target).correct
            is True
        )
        assert (
            next(o for o in second.methods["always_cheap"].outcomes if o.item_id == target).correct
            is False
        )
        assert view.public_inputs == altered.public_inputs
        assert keys == [c.prepared.request.identity_payload() for s in book.specs for c in s.cells]
        assert (
            ledger.connection.execute(
                "SELECT COUNT(*) FROM events WHERE json_extract(payload,'$.kind')='reuse_accounted'"
            ).fetchone()[0]
            == 60
        )
        assert ledger.totals()["dispatched_attempts"] == 0


def test_mutating_nested_frozen_labels_refuses_before_dispatch(tmp_path):
    view, source, policies, ledger, _, _ = fixture_bundle(tmp_path)
    view.records[0].label_payload.payload["answer"] = "999"
    with ledger, pytest.raises(ValueError, match="evaluation_frozen_records_changed"):
        run_comparison(view, source, policies)
    assert not source.executions


def test_synthetic_reference_cannot_be_relabelled_human():
    payload = dataset_bytes(make_controls())
    ids = [t.task_id for t in make_controls().tasks]
    reference = ReferenceAdmission(
        receipt_sha256="1" * 64,
        dataset_sha256=hashlib.sha256(payload).hexdigest(),
        label_sources={i: "human" for i in ids},
        provenance_refs={i: "invented-human" for i in ids},
        independent_seal_sha256=None,
        dev_config_sha256=None,
    )
    with pytest.raises(ValueError, match="synthetic_reference_cannot_promote"):
        prepare_view(
            payload,
            frozen_at="2026-10-14T00:00:00Z",
            method_configs={"method": "2" * 64},
            all_synthetic_dev=True,
            reference_admission=reference,
            trusted_reference_receipts={
                reference.receipt_sha256: canonical_digest(reference.model_dump(mode="json"))
            },
        )


def test_frozen_data_flag_requires_separate_existing_dataset_authority():
    value = make_controls().model_dump(mode="json")
    value["evidence_status"] = "frozen_corpus"
    with pytest.raises(ValueError, match="frozen_admission_required"):
        prepare_view(
            json.dumps(value).encode(), frozen_at="2026-10-14T00:00:00Z", method_configs={}
        )


def test_public_projection_rejects_hidden_fields_and_heldout_requires_real_admission(tmp_path):
    view, source, policies, ledger, _, _ = fixture_bundle(tmp_path)
    gold = view.split_records("dev")[0]
    value = source.lookup(gold)
    bad = canonical_runner.SourceView(
        value.status, value.input_sha256, value.public_payload | {"rubric": "private"}
    )
    with pytest.raises(ValueError, match="public_prompt_projection"):
        AnswerAdapter(view.manifest.publication_policy).to_public_input(gold, bad)
    with ledger, pytest.raises(FrozenCorpusError, match="heldout_admission_required"):
        run_comparison(view, source, policies, split="heldout")
    assert not source.executions


def test_changed_method_config_refuses_before_source_use(tmp_path):
    view, source, policies, ledger, _, _ = fixture_bundle(tmp_path)
    policies[0].config.confidence_thresholds[0] = 0.9
    with ledger, pytest.raises(ValueError, match="evaluation_method_configuration_changed"):
        run_comparison(view, source, policies)
    assert not source.executions


def test_failed_collection_is_not_implicitly_retried_by_seven_methods(tmp_path, monkeypatch):
    view, source, policies, ledger, _, _ = fixture_bundle(tmp_path)
    attempts = []

    def failed(spec, **kwargs):
        attempts.append(spec.item.prompt_id)
        raise ValueError("SYNTHETIC failed collection control")

    monkeypatch.setattr(engine, "collect_matrix", failed)
    with ledger:
        result = run_comparison(view, source, policies).comparison
        assert len(attempts) == len(set(attempts)) == 10
        assert all(
            len(m.outcomes) == 10 and not m.actual_predictions for m in result.methods.values()
        )
        assert all(
            o.status == "provider_error" for m in result.methods.values() for o in m.outcomes
        )
        assert ledger.totals()["dispatched_attempts"] == 0


@pytest.mark.parametrize(
    "mutation", ["none", "no_reference", "untrusted", "wrong_bytes", "missing_id"]
)
def test_frozen_reference_port_needs_exact_external_trust_double(mutation):
    """300-row structural trust double only: no real corpus, labels, rights or exposure."""
    originals = make_controls()
    source = originals.sources[0].model_copy(
        update={
            "role": "benchmark_source",
            "rights_receipt_sha256": digest("UNIT fake rights"),
            "author_id": "UNIT-independent-double",
        }
    )
    tasks = []
    for kind, count in COUNTS.items():
        template = next(t for t in originals.tasks if t.kind == kind)
        for index in range(count):
            row = template.model_dump(mode="json", exclude={"row_sha256"})
            row.update(
                task_id=f"unit-{kind}-{index}",
                family_id=f"unit-{kind}-{index // 2}",
                source_row_id=f"unit-{kind}-{index}",
                prompt=f"{template.prompt} UNIT variant {index}.",
            )
            tasks.append(Task.model_validate(row | {"row_sha256": digest(row)}))
    dataset = Dataset(
        schema_version=1, evidence_status="frozen_corpus", sources=[source], tasks=tasks
    )
    payload = dataset_bytes(dataset)
    grant = DatasetAdmission(
        dataset_sha256=hashlib.sha256(payload).hexdigest(),
        source_grants={source.source_id: source},
        freeze_receipt_sha256=digest("UNIT fake freeze"),
        independent_author_ids=[source.author_id],
        excluded_tuner_ids=["UNIT-tuner"],
    )
    reference = ReferenceAdmission(
        receipt_sha256=digest("UNIT fake reference receipt"),
        dataset_sha256=grant.dataset_sha256,
        label_sources={t.task_id: "agent_provisional" for t in tasks},
        provenance_refs={t.task_id: "UNIT-not-a-human-label" for t in tasks},
        independent_seal_sha256=None,
        dev_config_sha256=None,
    )
    if mutation == "wrong_bytes":
        reference = reference.model_copy(update={"dataset_sha256": "0" * 64})
    if mutation == "missing_id":
        reference.label_sources.pop(tasks[0].task_id)
    trusted = {reference.receipt_sha256: canonical_digest(reference.model_dump(mode="json"))}
    kwargs = dict(
        dataset_admission=grant,
        reference_admission=None if mutation == "no_reference" else reference,
        trusted_reference_receipts={} if mutation == "untrusted" else trusted,
        frozen_at="2026-10-14T00:00:00Z",
        method_configs={"method": "2" * 64},
    )
    if mutation != "none":
        with pytest.raises(
            ValueError, match="reference_admission_required|reference_origin_admission"
        ):
            prepare_view(payload, **kwargs)
    else:
        view = prepare_view(payload, **kwargs)
        assert len(view.split_records("dev")) == len(view.split_records("heldout")) == 150
        assert all(r.label_source == "agent_provisional" for r in view.records)
        assert all(ref.status == "not_available" for ref in view.manifest.experiment_refs.values())
