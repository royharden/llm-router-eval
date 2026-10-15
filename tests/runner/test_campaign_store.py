"""Actual metadata SQLite/Provider/owner counters. UNIT grants are not real experiment authority."""

import json
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest
from test_offline_matrix import book_for, collect, inputs, open_ledger

from llm_router_eval.data.controls import make_controls
from llm_router_eval.evaluation.campaign import (
    CampaignBinding,
    CampaignError,
    CampaignGrant,
    CampaignStore,
    capture_book_digest,
    database_location_sha256,
    synthetic_dev_grant,
)
from llm_router_eval.evaluation.matrix import build_matrix_spec
from llm_router_eval.shared.callledger import Ledger
from llm_router_eval.shared.callledger.models import canonical_digest as h
from llm_router_eval.shared.evalkit.runner import HeldoutAdmission, HeldoutHistory
from llm_router_eval.shared.providers import Provider
from llm_router_eval.shared.providers.models import Result
from llm_router_eval.shared.routing.policies import Policy


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*a, **kw):
        raise RuntimeError("campaign network forbidden")

    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)


def binding_for(book, **changes):
    value = dict(
        state_id="UNIT-state",
        campaign_id="UNIT-campaign",
        split="dev",
        evidence_status="synthetic_implementation",
        mode="mock",
        seal_sha256=h("UNIT seal"),
        source_book_sha256=capture_book_digest(book.records),
        public_config_sha256=book.specs[0].config.sha256,
        assigned_ids=[s.item.prompt_id for s in book.specs],
        method_config_sha256={
            n: h([n, "UNIT config"])
            for n in [
                "always_cheap",
                "always_frontier",
                "rules",
                "classifier",
                "jev_gate",
                "cascade_jev",
                "cascade_llm",
            ]
        },
        manifest_sha256=h("UNIT manifest"),
        independent_seal_sha256=None,
        dev_config_sha256=None,
        history_ref="UNIT-history",
        source_revision="UNIT-source-revision",
    )
    value.update(changes)
    return CampaignBinding.model_validate(value)


def store_for(path, book, binding=None):
    binding = binding or binding_for(book)
    grant, trusted = synthetic_dev_grant(binding, path)
    return CampaignStore(path, binding=binding, grant=grant, trusted_grants=trusted)


class Retained:
    """Explicit retained-result test reader/writer; values come from actual Provider results."""

    def __init__(self):
        self.values = {}

    def write(self, digest, result):
        self.values[digest] = result.model_dump(mode="json")

    def read(self, digest):
        return Result.model_validate(self.values[digest])


def run(store, book, ledger, archive, **kw):
    authority, _ = inputs()
    return store.collect(
        book.specs[0],
        ledger=ledger,
        budget=authority,
        records=book.records,
        retained_result_reader=archive.read,
        retained_result_writer=archive.write,
        **kw,
    )


def test_partial_interruption_and_reopen_execute_only_missing_cells_preserving_original_sources(
    tmp_path, monkeypatch
):
    book = book_for()
    archive = Retained()
    path = tmp_path / "campaign.sqlite3"
    original = Provider.run
    calls = []

    def counted(self, *args, **kw):
        calls.append(kw["context"].child_id)
        return original(self, *args, **kw)

    monkeypatch.setattr(Provider, "run", counted)
    writes = 0

    def interrupted(digest, result):
        nonlocal writes
        writes += 1
        archive.write(digest, result)
        if writes == 3:
            raise RuntimeError("UNIT interruption before third metadata commit")

    with open_ledger(tmp_path, book) as ledger, store_for(path, book) as store:
        authority, _ = inputs()
        with pytest.raises(RuntimeError, match="UNIT interruption"):
            store.collect(
                book.specs[0],
                ledger=ledger,
                budget=authority,
                records=book.records,
                retained_result_writer=interrupted,
            )
        assert calls == ["cheap", "frontier", "classifier"]
        assert [cell for _, cell in store.missing()] == [
            "classifier",
            "gate",
            "check_jev",
            "check_llm",
        ]
        assert (
            ledger.connection.execute(
                "SELECT COUNT(*) FROM children WHERE state='pending'"
            ).fetchone()[0]
            == 0
        )
        old = store.restore(book.specs[0], ledger=ledger, retained_result_reader=archive.read)
        source_ids = {n: o.result.accounting_ref for n, o in old.items()}
    calls.clear()
    with open_ledger(tmp_path, book) as ledger, store_for(path, book) as store:
        matrix = run(store, book, ledger, archive)
        assert calls == ["classifier", "gate", "check_jev", "check_llm"]
        assert not store.missing()
        assert all(matrix.cell(n).restored for n in ["cheap", "frontier"])
        assert all(matrix.cell(n).result.accounting_ref == source_ids[n] for n in source_ids)
        assert matrix.execute(Policy("cascade_jev"), ledger=ledger).answer.text == "FINAL: 5"
        assert ledger.totals()["dispatched_attempts"] == 0
        # The interrupted third read is retained in the owner ledger; it is not hidden or paid.
        rows = [json.loads(r[0]) for r in ledger.connection.execute("SELECT payload FROM events")]
        assert sum(r["kind"] == "reuse_accounted" for r in rows) == 7


def test_fully_completed_resume_uses_reader_and_zero_new_provider_calls(tmp_path, monkeypatch):
    book = book_for()
    archive = Retained()
    path = tmp_path / "campaign.sqlite3"
    with open_ledger(tmp_path, book) as ledger, store_for(path, book) as store:
        first = run(store, book, ledger, archive)
        before = list(ledger.connection.iterdump())

        def never(*a, **kw):
            raise AssertionError("completed source must not dispatch or lookup again")

        monkeypatch.setattr(Provider, "run", never)
        restored = run(store, book, ledger, archive)
        assert all(
            restored.cell(n).restored
            for n in ["cheap", "frontier", "classifier", "gate", "check_jev", "check_llm"]
        )
        assert restored.cell("cheap").result == first.cell("cheap").result
        assert before == list(ledger.connection.iterdump())


@pytest.mark.parametrize("failure", ["missing_reader", "changed_result", "missing_owner"])
def test_retained_reader_cannot_supply_new_or_unowned_results(tmp_path, failure):
    book = book_for()
    archive = Retained()
    path = tmp_path / "campaign.sqlite3"
    with open_ledger(tmp_path, book) as ledger, store_for(path, book) as store:
        run(store, book, ledger, archive)
        called = []

        def changed(key):
            called.append(True)
            return archive.read(key).model_copy(update={"text": "FINAL: CANARY_CHANGED_RESULT"})

        if failure == "missing_reader":
            with pytest.raises(CampaignError, match="retained_result_reader_required"):
                store.restore(book.specs[0], ledger=ledger)
        elif failure == "changed_result":
            with pytest.raises(CampaignError, match="retained_result_changed"):
                store.restore(book.specs[0], ledger=ledger, retained_result_reader=changed)
        else:
            authority, _ = inputs()
            with Ledger(
                tmp_path / "different-owner.sqlite3",
                authority=authority,
                trusted_requests=book.request_evidence,
            ) as absent:
                with pytest.raises(ValueError, match="allocation_mismatch"):
                    store.restore(book.specs[0], ledger=absent, retained_result_reader=changed)
                assert not called


def test_actual_owner_scope_is_checked_before_each_private_payload_reader(tmp_path, monkeypatch):
    book = book_for()
    archive = Retained()
    order = []
    with (
        open_ledger(tmp_path, book) as ledger,
        store_for(tmp_path / "campaign.sqlite3", book) as store,
    ):
        run(store, book, ledger, archive)
        real = ledger.child_counters

        def observed(*a, **kw):
            order.append("owner")
            return real(*a, **kw)

        def read(key):
            assert order[-1] == "owner"
            order.append("reader")
            return archive.read(key)

        monkeypatch.setattr(ledger, "child_counters", observed)
        assert len(store.restore(book.specs[0], ledger=ledger, retained_result_reader=read)) == 6
        assert order.count("reader") == 6


def test_metadata_database_contains_no_raw_payload_and_writer_cannot_mutate_observation(tmp_path):
    book = book_for()
    archive = Retained()
    path = tmp_path / "campaign.sqlite3"

    def writer(key, result):
        archive.write(key, result)
        if result.parsed is not None:
            result.parsed["CANARY_WRITER_MUTATION"] = "secret"

    with open_ledger(tmp_path, book) as ledger, store_for(path, book) as store:
        authority, _ = inputs()
        matrix = store.collect(
            book.specs[0],
            ledger=ledger,
            budget=authority,
            records=book.records,
            retained_result_writer=writer,
        )
        assert "CANARY_WRITER_MUTATION" not in json.dumps(matrix.cell("classifier").result.parsed)
        restored = store.restore(book.specs[0], ledger=ledger, retained_result_reader=archive.read)
        assert len(restored) == 6
        text = "\n".join(store.connection.iterdump())
        canaries = [
            book.specs[0].item.text,
            "FINAL: 5",
            "CANARY_WRITER_MUTATION",
            "Classify the prompt data",
        ]

        def leaked(value):
            return any(c in value for c in canaries)

        assert not leaked(text) and leaked(text + canaries[0])


@pytest.mark.parametrize("change", ["path", "grant", "state", "seal", "source_book"])
def test_path_grant_state_and_configuration_cannot_silently_reset_history(tmp_path, change):
    book = book_for()
    path = tmp_path / "campaign.sqlite3"
    binding = binding_for(book)
    grant, trusted = synthetic_dev_grant(binding, path)
    with CampaignStore(path, binding=binding, grant=grant, trusted_grants=trusted) as store:
        original = list(store.connection.iterdump())
    target = path
    if change == "path":
        target = tmp_path / "different.sqlite3"
    elif change == "grant":
        trusted = {}
    else:
        field = {"state": "state_id", "seal": "seal_sha256", "source_book": "source_book_sha256"}[
            change
        ]
        binding = binding.model_copy(
            update={field: "different-state" if change == "state" else h("different")}
        )
        grant, trusted = synthetic_dev_grant(binding, path)
    with pytest.raises(CampaignError, match="campaign_authority|campaign_configuration_changed"):
        CampaignStore(target, binding=binding, grant=grant, trusted_grants=trusted)
    if change == "path":
        assert not target.exists()
    binding = binding_for(book)
    grant, trusted = synthetic_dev_grant(binding, path)
    with CampaignStore(path, binding=binding, grant=grant, trusted_grants=trusted) as store:
        assert list(store.connection.iterdump()) == original


def test_changed_source_book_or_checker_requests_refuse_before_any_new_lookup(
    tmp_path, monkeypatch
):
    book = book_for()
    archive = Retained()
    path = tmp_path / "campaign.sqlite3"
    with open_ledger(tmp_path, book) as ledger, store_for(path, book) as store:
        run(store, book, ledger, archive)
        calls = []
        monkeypatch.setattr(Provider, "run", lambda *a, **kw: calls.append(True))
        changed = book_for(cost_overrides={(book.specs[0].item.prompt_id, "cheap"): "0.002"})
        authority, _ = inputs()
        with pytest.raises(CampaignError, match="campaign_source_book_changed"):
            store.collect(book.specs[0], ledger=ledger, budget=authority, records=changed.records)
        spec = book.specs[0]
        altered = build_matrix_spec(
            spec.item,
            config=spec.config,
            authority=authority,
            limits=spec.limits,
            run_id=spec.root_context.run_id,
            operation_id="new-context",
            cheap_text="FINAL: different",
        )
        with pytest.raises(CampaignError, match="campaign_requests_changed"):
            store.restore(altered, ledger=ledger, retained_result_reader=archive.read)
        assert not calls


def test_duplicate_cell_never_replaces_original_completion(tmp_path):
    book = book_for()
    with (
        open_ledger(tmp_path, book) as ledger,
        store_for(tmp_path / "campaign.sqlite3", book) as store,
    ):
        matrix = collect(book, ledger)
        cell = matrix.cell("cheap")
        store.record_cell(book.specs[0], cell, ledger=ledger)
        original = list(store.connection.iterdump())
        with pytest.raises(CampaignError, match="campaign_cell_exists"):
            store.record_cell(book.specs[0], cell, ledger=ledger)
        assert original == list(store.connection.iterdump())


def heldout_double(book, path):
    """Explicit UNIT owner-authority double, not a real independent seal or permission."""
    binding = binding_for(
        book,
        split="heldout",
        evidence_status="frozen_corpus",
        mode="strict_replay",
        independent_seal_sha256=h("UNIT independent-seal double"),
        dev_config_sha256=h("UNIT dev lock"),
        source_revision=h("UNIT source revision double"),
    )
    grant = CampaignGrant(
        grant_sha256=h("UNIT heldout owner grant double"),
        binding_sha256=binding.sha256,
        state_id=binding.state_id,
        database_location_sha256=database_location_sha256(path),
        heldout_allowed=True,
    )
    trusted = {grant.grant_sha256: h(grant.model_dump(mode="json"))}
    admission = HeldoutAdmission(
        binding.manifest_sha256,
        binding.independent_seal_sha256,
        binding.dev_config_sha256,
        binding.method_config_sha256,
        binding.source_revision,
        binding.history_ref,
        None,
    )
    return binding, grant, trusted, admission


def test_canonical_heldout_history_persists_once_only_and_rejects_changed_scope(tmp_path):
    book = book_for()
    path = tmp_path / "heldout.sqlite3"
    binding, grant, trusted, admission = heldout_double(book, path)
    with CampaignStore(path, binding=binding, grant=grant, trusted_grants=trusted) as store:
        assert isinstance(store, HeldoutHistory)
        with pytest.raises(CampaignError, match="heldout_frozen_admission_changed"):
            store.record_exposure(replace(admission, dev_config_sha256=h("changed")))
        assert store.connection.execute("SELECT COUNT(*) FROM exposure").fetchone()[0] == 0
        store.record_exposure(admission)
    with CampaignStore(path, binding=binding, grant=grant, trusted_grants=trusted) as store:
        with pytest.raises(CampaignError, match="heldout_already_exposed"):
            store.record_exposure(admission)
        with pytest.raises(CampaignError, match="heldout_frozen_admission_changed"):
            store.record_exposure(replace(admission, rerun_reason="a reason is not new authority"))


def test_synthetic_helpers_and_records_cannot_authorize_real_heldout(tmp_path):
    book = book_for()
    path = tmp_path / "heldout.sqlite3"
    binding, grant, trusted, admission = heldout_double(book, path)
    with pytest.raises(CampaignError, match="synthetic_dev_only"):
        synthetic_dev_grant(binding, path)
    with pytest.raises(CampaignError, match="campaign_authority"):
        CampaignStore(path, binding=binding, grant=grant, trusted_grants={})
    with (
        open_ledger(tmp_path, book) as ledger,
        CampaignStore(path, binding=binding, grant=grant, trusted_grants=trusted) as store,
    ):
        store.record_exposure(admission)
        authority, _ = inputs()
        with pytest.raises(CampaignError, match="synthetic_source_not_benchmark"):
            store.collect(book.specs[0], ledger=ledger, budget=authority, records=book.records)
        assert ledger.connection.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 0


def test_unrelated_file_is_preserved(tmp_path):
    book = book_for()
    path = tmp_path / "unrelated.sqlite3"
    path.write_bytes(b"UNRELATED_KEEP_THIS_FILE")
    binding = binding_for(book)
    grant, trusted = synthetic_dev_grant(binding, path)
    with pytest.raises(CampaignError, match="unsafe_campaign_store"):
        CampaignStore(path, binding=binding, grant=grant, trusted_grants=trusted)
    assert path.read_bytes() == b"UNRELATED_KEEP_THIS_FILE"


def test_duplicate_cell_race_has_one_commit_and_one_rejection(tmp_path):
    book = book_for()
    path = tmp_path / "race.sqlite3"
    binding = binding_for(book)
    grant, trusted = synthetic_dev_grant(binding, path)
    with open_ledger(tmp_path, book) as ledger:
        matrix = collect(book, ledger)
        cell = matrix.cell("cheap")

    def write(_):
        with (
            open_ledger(tmp_path, book) as owner,
            CampaignStore(path, binding=binding, grant=grant, trusted_grants=trusted) as store,
        ):
            try:
                store.record_cell(book.specs[0], cell, ledger=owner)
                return "committed"
            except CampaignError as error:
                assert str(error) == "campaign_cell_exists"
                return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(write, range(2))) == ["committed", "rejected"]
    with CampaignStore(path, binding=binding, grant=grant, trusted_grants=trusted) as store:
        assert store.connection.execute("SELECT COUNT(*) FROM cells").fetchone()[0] == 1


def test_actual_process_exit_before_cell_commit_rolls_back_partial_record(tmp_path):
    """The owned subprocess really exits23 inside the production cell transaction."""
    book = book_for()
    path = tmp_path / "crash.sqlite3"
    binding = binding_for(book)
    grant, trusted = synthetic_dev_grant(binding, path)
    with open_ledger(tmp_path, book) as ledger:
        matrix = collect(book, ledger)
        cell = matrix.cell("cheap")
    seed = tmp_path / "explicit-private-test-result.json"
    seed.write_text(
        json.dumps(
            dict(
                name=cell.name,
                span_id=cell.span_id,
                request_identity_sha256=cell.request_identity_sha256,
                result=cell.result.model_dump(mode="json"),
                reason=cell.reason,
                allocation=cell.allocation.model_dump(mode="json"),
                context=cell.context.model_dump(mode="json"),
            )
        )
    )
    script = r"""
import json,os,socket,sys
from pathlib import Path
def denied(*a,**kw):raise RuntimeError('child network forbidden')
socket.create_connection=denied;socket.socket.connect=denied
sys.path.insert(0,sys.argv[1])
from test_offline_matrix import book_for,open_ledger
from test_campaign_store import binding_for
from llm_router_eval.evaluation.campaign import CampaignStore,synthetic_dev_grant
from llm_router_eval.evaluation.matrix import CellObservation
from llm_router_eval.shared.callledger.models import Allocation,CallContext
from llm_router_eval.shared.providers.models import Result
class Interrupted(CampaignStore):
    def _checkpoint(self,phase):
        if phase=='cell_written':os._exit(23)
root=Path(sys.argv[2]);book=book_for();path=root/'crash.sqlite3';binding=binding_for(book)
grant,trusted=synthetic_dev_grant(binding,path)
data=json.loads((root/'explicit-private-test-result.json').read_text())
data['allocation']=Allocation.model_validate(data['allocation']);data['context']=CallContext.model_validate(data['context']);data['result']=Result.model_validate(data['result'])
with (
    open_ledger(root,book) as ledger,
    Interrupted(path,binding=binding,grant=grant,trusted_grants=trusted) as store,
):
    store.record_cell(book.specs[0],CellObservation(**data),ledger=ledger)
raise AssertionError('actual fault injection did not execute')
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(Path(__file__).parent), str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 23, completed.stdout + completed.stderr
    with CampaignStore(path, binding=binding, grant=grant, trusted_grants=trusted) as store:
        assert store.connection.execute("SELECT COUNT(*) FROM cells").fetchone()[0] == 0
        assert len(store.missing()) == 6


def test_actual_other_task_owner_record_is_rejected_before_private_reader(tmp_path):
    book = book_for(make_controls().tasks[:2])
    archive = Retained()
    path = tmp_path / "campaign.sqlite3"
    authority, _ = inputs()
    with open_ledger(tmp_path, book) as ledger, store_for(path, book) as store:
        for spec in book.specs:
            store.collect(
                spec,
                ledger=ledger,
                budget=authority,
                records=book.records,
                retained_result_writer=archive.write,
            )
        other = store.connection.execute(
            "SELECT record_json,record_sha FROM cells WHERE task_id=? AND cell='cheap'",
            (book.specs[1].item.prompt_id,),
        ).fetchone()
        store.connection.execute(
            "UPDATE cells SET record_json=?,record_sha=? WHERE task_id=? AND cell='cheap'",
            (other[0], other[1], book.specs[0].item.prompt_id),
        )
        called = []

        def read(key):
            called.append(True)
            return archive.read(key)

        with pytest.raises(CampaignError, match="campaign_owner_scope"):
            store.restore(book.specs[0], ledger=ledger, retained_result_reader=read)
        assert not called


def test_never_run_checker_metadata_restores_without_fake_spans_or_payloads(tmp_path):
    book = book_for()
    archive = Retained()
    records = tuple(
        r for r, c in zip(book.records, book.specs[0].cells, strict=True) if c.name != "cheap"
    )
    binding = binding_for(book, source_book_sha256=capture_book_digest(records))
    with (
        open_ledger(tmp_path, book) as ledger,
        store_for(tmp_path / "campaign.sqlite3", book, binding) as store,
    ):
        authority, _ = inputs()
        first = store.collect(
            book.specs[0],
            ledger=ledger,
            budget=authority,
            records=records,
            retained_result_writer=archive.write,
        )
        assert first.cell("check_jev").result is None and first.cell("check_jev").span_id is None
        restored = store.collect(
            book.specs[0],
            ledger=ledger,
            budget=authority,
            records=records,
            retained_result_reader=archive.read,
        )
        projected = {r["cell"]: r for r in restored.safe_projection()}
        assert projected["check_jev"]["status"] == "not_run"
        assert (
            projected["check_jev"]["span_id"] is None
            and projected["check_jev"]["lookup_latency_ms"] is None
        )
        assert len(archive.values) == 4 and not store.missing()


def test_actual_bootstrap_exit_releases_os_lock_and_reopens_without_stale_pid_cleanup(tmp_path):
    book = book_for()
    path = tmp_path / "bootstrap.sqlite3"
    binding = binding_for(book)
    grant, trusted = synthetic_dev_grant(binding, path)
    script = r"""
import os,socket,sys
from pathlib import Path
def denied(*a,**kw):raise RuntimeError('child network forbidden')
socket.create_connection=denied;socket.socket.connect=denied
sys.path.insert(0,sys.argv[1])
from test_offline_matrix import book_for
from test_campaign_store import binding_for
from llm_router_eval.evaluation.campaign import CampaignStore,synthetic_dev_grant
class Interrupted(CampaignStore):
    def _checkpoint(self,phase):
        if phase=='store_schema_written':os._exit(24)
path=Path(sys.argv[2])/'bootstrap.sqlite3';binding=binding_for(book_for())
grant,trusted=synthetic_dev_grant(binding,path)
Interrupted(path,binding=binding,grant=grant,trusted_grants=trusted)
raise AssertionError('actual bootstrap fault did not execute')
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(Path(__file__).parent), str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 24, completed.stdout + completed.stderr
    with CampaignStore(path, binding=binding, grant=grant, trusted_grants=trusted) as store:
        assert len(store.missing()) == 6
        assert store.connection.execute("SELECT COUNT(*) FROM cells").fetchone()[0] == 0
