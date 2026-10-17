"""App-owned durable experiment metadata, not a money ledger or implicit payload cache."""

import errno
import hashlib
import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Literal, get_args
from uuid import uuid4

from pydantic import Field, model_validator

from ..shared.callledger.models import (
    Allocation,
    CallContext,
    Digest,
    Identifier,
    WireModel,
    canonical_digest,
)
from ..shared.evalkit.runner import HeldoutAdmission, HeldoutHistory
from ..shared.providers import FixtureIndex
from ..shared.providers.models import Result
from ..shared.routing.policies import PolicyName
from .matrix import (
    CELL_PURPOSES,
    CellObservation,
    ObservationMatrix,
    build_matrix_spec,
    collect_matrix,
    validate_retained_observation,
)


class CampaignError(ValueError):
    """Closed code only; no source/response/database/path contents in diagnostics."""


@contextmanager
def _bootstrap_lock(path):
    """OS-owned lock avoids treating a peer's new empty SQLite file as unrelated data.

    The lock handle releases on process exit; no PID guessing or stale-lock deletion.
    The adjacent lock file contains no payload and is never overwritten or removed.
    """
    lock_path = path.with_name(path.name + ".bootstrap.lock")
    if lock_path.is_symlink() or (lock_path.exists() and not lock_path.is_file()):
        raise CampaignError("unsafe_campaign_store")
    with lock_path.open("a+b") as stream:
        if os.name == "nt":
            import msvcrt

            def acquire():
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)

            def release():
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            def acquire():
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

            def release():
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

        deadline = time.monotonic() + 5
        while True:
            try:
                acquire()
                break
            except OSError as error:
                if error.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise CampaignError("campaign_bootstrap_lock") from None
                if time.monotonic() >= deadline:
                    raise CampaignError("campaign_bootstrap_busy") from None
                time.sleep(0.025)
        try:
            yield
        finally:
            release()


class CampaignBinding(WireModel):
    state_id: Identifier
    campaign_id: Identifier
    split: Literal["dev", "heldout"]
    evidence_status: Literal["synthetic_implementation", "frozen_corpus"]
    mode: Literal["mock", "strict_replay"]
    seal_sha256: Digest
    source_book_sha256: Digest
    public_config_sha256: Digest
    assigned_ids: Annotated[list[Identifier], Field(min_length=1, max_length=300)]
    method_config_sha256: dict[Identifier, Digest]
    manifest_sha256: Digest
    independent_seal_sha256: Digest | None
    dev_config_sha256: Digest | None
    history_ref: Identifier
    source_revision: Identifier

    @model_validator(mode="after")
    def fixed_population(self):
        if len(set(self.assigned_ids)) != len(self.assigned_ids) or set(
            self.method_config_sha256
        ) != set(get_args(PolicyName)):
            raise ValueError("campaign_population")
        if self.split == "heldout" and (
            self.evidence_status != "frozen_corpus"
            or self.independent_seal_sha256 is None
            or self.dev_config_sha256 is None
            or self.mode != "strict_replay"
            or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", self.source_revision) is None
        ):
            raise ValueError("heldout_independent_inputs_required")
        return self

    @property
    def sha256(self):
        return canonical_digest(self.model_dump(mode="json"))


class CampaignGrant(WireModel):
    grant_sha256: Digest
    binding_sha256: Digest
    state_id: Identifier
    database_location_sha256: Digest
    heldout_allowed: bool


def database_location_sha256(path):
    return canonical_digest(os.path.normcase(str(Path(path).resolve())))


def synthetic_dev_grant(binding: CampaignBinding, path):
    """Self-authored DEV controls only. This cannot authorize frozen data or heldout exposure."""
    binding = CampaignBinding.model_validate(binding.model_dump(mode="json"))
    if (
        binding.evidence_status != "synthetic_implementation"
        or binding.split != "dev"
        or binding.mode != "mock"
    ):
        raise CampaignError("synthetic_dev_only")
    grant = CampaignGrant(
        grant_sha256=canonical_digest(
            ["authored-dev-control", binding.sha256, database_location_sha256(path)]
        ),
        binding_sha256=binding.sha256,
        state_id=binding.state_id,
        database_location_sha256=database_location_sha256(path),
        heldout_allowed=False,
    )
    return grant, {grant.grant_sha256: canonical_digest(grant.model_dump(mode="json"))}


def capture_book_digest(records):
    """Bind actual supplied immutable capture bytes and source metadata, not an input self-flag."""
    return canonical_digest(
        [
            dict(
                content_sha256=hashlib.sha256(r.content_json).hexdigest(),
                source=r.source.model_dump(mode="json"),
                namespace=r.namespace,
                generation=r.source_generation,
                context=r.consumer_context_sha256,
                envelope=r.dispatched_envelope_sha256,
                review=r.review_receipt_sha256,
            )
            for r in records
        ]
    )


class StoredCell(WireModel):
    task_id: Identifier
    cell: Literal["cheap", "frontier", "classifier", "gate", "check_jev", "check_llm"]
    span_id: Identifier | None
    request_identity_sha256: Digest
    allocation: Allocation
    context: CallContext
    result_sha256: Digest | None
    capture_sha256: Digest | None
    reason: Literal["collected", "cheap_unavailable"]


def validate_campaign_authority(path, *, binding, grant, trusted_grants):
    """The existing store admission, without creating a directory or database."""
    binding = CampaignBinding.model_validate(binding.model_dump(mode="json"))
    grant = CampaignGrant.model_validate(grant.model_dump(mode="json"))
    if (
        trusted_grants.get(grant.grant_sha256) != canonical_digest(grant.model_dump(mode="json"))
        or grant.binding_sha256 != binding.sha256
        or grant.state_id != binding.state_id
        or grant.database_location_sha256 != database_location_sha256(path)
        or (binding.split == "heldout" and not grant.heldout_allowed)
    ):
        raise CampaignError("campaign_authority")
    return binding, grant


def validate_exposure(binding, grant, admission):
    """Pure field binding shared by replay preflight and durable exposure recording."""
    if (
        binding.split != "heldout"
        or not grant.heldout_allowed
        or not isinstance(admission, HeldoutAdmission)
        or admission.manifest_sha256 != binding.manifest_sha256
        or admission.independent_seal_sha256 != binding.independent_seal_sha256
        or admission.dev_config_sha256 != binding.dev_config_sha256
        or admission.method_config_sha256 != binding.method_config_sha256
        or admission.history_ref != binding.history_ref
        or admission.candidate_commit != binding.source_revision
        or admission.rerun_reason is not None
    ):
        raise CampaignError("heldout_frozen_admission_changed")


class CampaignStore(HeldoutHistory):
    """Trusted caller supplies authority separately from data. Stores metadata/hashes only.

    Binding a location/state prevents casual copies or different output roots from resetting
    history. It is not tamper resistance against an owner deleting/replacing the physical state.
    """

    def __init__(self, path, *, binding: CampaignBinding, grant: CampaignGrant, trusted_grants):
        self.binding, self.grant = validate_campaign_authority(
            path, binding=binding, grant=grant, trusted_grants=trusted_grants
        )
        self._binding_sha256 = self.binding.sha256
        self.path = Path(path).resolve()
        original = Path(path)
        if original.is_symlink() or (original.exists() and not original.is_file()):
            raise CampaignError("unsafe_campaign_store")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _bootstrap_lock(self.path):
            self._open_store(original)

    def _open_store(self, original):
        if original.exists():
            with original.open("rb") as stream:
                # A trusted new-store grant also covers an empty allocation left
                # by a process that exited before SQLite wrote its first header.
                if stream.read(16) not in {b"", b"SQLite format 3\x00"}:
                    raise CampaignError("unsafe_campaign_store")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        try:
            tables = {
                r[0]
                for r in self.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if tables - {"identity", "tasks", "cells", "exposure"}:
                raise CampaignError("unsafe_campaign_store")
            if self.connection.execute("PRAGMA user_version").fetchone()[0] not in {0, 1}:
                raise CampaignError("campaign_version")
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA synchronous=FULL")
            with self._transaction():
                self.connection.execute(
                    "CREATE TABLE IF NOT EXISTS identity (slot INTEGER PRIMARY KEY CHECK(slot=1), "
                    "binding_sha TEXT, binding_json TEXT, grant_sha TEXT, location_sha TEXT)"
                )
                self.connection.execute(
                    "CREATE TABLE IF NOT EXISTS tasks "
                    "(task_id TEXT PRIMARY KEY, request_set_sha TEXT NOT NULL)"
                )
                self.connection.execute(
                    "CREATE TABLE IF NOT EXISTS cells (task_id TEXT, cell TEXT, "
                    "record_json TEXT NOT NULL, record_sha TEXT NOT NULL, "
                    "PRIMARY KEY(task_id,cell))"
                )
                self.connection.execute(
                    "CREATE TABLE IF NOT EXISTS exposure (slot INTEGER PRIMARY KEY CHECK(slot=1), "
                    "admission_json TEXT NOT NULL, admission_sha TEXT NOT NULL)"
                )
                prior = self.connection.execute("SELECT * FROM identity WHERE slot=1").fetchone()
                expected = (
                    self.binding.sha256,
                    self.grant.grant_sha256,
                    self.grant.database_location_sha256,
                )
                if prior is None:
                    self.connection.execute(
                        "INSERT INTO identity VALUES(1,?,?,?,?)",
                        (
                            self.binding.sha256,
                            json.dumps(self.binding.model_dump(mode="json"), sort_keys=True),
                            self.grant.grant_sha256,
                            self.grant.database_location_sha256,
                        ),
                    )
                elif (prior["binding_sha"], prior["grant_sha"], prior["location_sha"]) != expected:
                    raise CampaignError("campaign_configuration_changed")
                self.connection.execute("PRAGMA user_version=1")
                self._checkpoint("store_schema_written")
        except BaseException:
            self.connection.close()
            raise

    @contextmanager
    def _transaction(self):
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def close(self):
        self.connection.close()

    def _checkpoint(self, _phase):
        """Owned test fault-injection seam; production performs no action."""

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def record_exposure(self, admission: HeldoutAdmission):
        b = self.binding
        if b.sha256 != self._binding_sha256:
            raise CampaignError("heldout_frozen_admission_changed")
        validate_exposure(b, self.grant, admission)
        payload = asdict(admission)
        with self._transaction():
            if self.connection.execute("SELECT 1 FROM exposure").fetchone():
                raise CampaignError("heldout_already_exposed")
            self.connection.execute(
                "INSERT INTO exposure VALUES(1,?,?)",
                (json.dumps(payload, sort_keys=True), canonical_digest(payload)),
            )

    def _check_spec(self, spec):
        spec.verify()
        if (
            self.binding.sha256 != self._binding_sha256
            or spec.item.prompt_id not in self.binding.assigned_ids
            or spec.config.sha256 != self.binding.public_config_sha256
        ):
            raise CampaignError("campaign_task_scope")
        request_set = canonical_digest(
            [
                (c.name, c.prepared.request_identity_sha256, c.prepared.dispatched_envelope_sha256)
                for c in spec.cells
            ]
        )
        return request_set

    def _admit_task(self, spec):
        request_set = self._check_spec(spec)
        with self._transaction():
            prior = self.connection.execute(
                "SELECT request_set_sha FROM tasks WHERE task_id=?", (spec.item.prompt_id,)
            ).fetchone()
            if prior and prior[0] != request_set:
                raise CampaignError("campaign_requests_changed")
            if prior is None:
                self.connection.execute(
                    "INSERT INTO tasks VALUES(?,?)", (spec.item.prompt_id, request_set)
                )

    def _cell_record(self, spec, observation, ledger):
        self._check_spec(spec)
        validate_retained_observation(spec, observation, ledger)
        result = observation.result
        if (
            self.binding.evidence_status == "frozen_corpus"
            and result is not None
            and result.origin != "replay"
        ):
            raise CampaignError("synthetic_source_not_benchmark")
        return StoredCell(
            task_id=spec.item.prompt_id,
            cell=observation.name,
            span_id=observation.span_id,
            request_identity_sha256=observation.request_identity_sha256,
            allocation=observation.allocation,
            context=observation.context,
            result_sha256=None
            if result is None
            else canonical_digest(result.model_dump(mode="json")),
            capture_sha256=None
            if result is None or result.source_capture is None
            else result.source_capture.capture_sha256,
            reason=observation.reason,
        )

    def record_cell(self, spec, observation, *, ledger):
        record = self._cell_record(spec, observation, ledger)
        self._admit_task(spec)
        payload = record.model_dump(mode="json")
        with self._transaction():
            if self.connection.execute(
                "SELECT 1 FROM cells WHERE task_id=? AND cell=?", (record.task_id, record.cell)
            ).fetchone():
                raise CampaignError("campaign_cell_exists")
            self.connection.execute(
                "INSERT INTO cells VALUES(?,?,?,?)",
                (
                    record.task_id,
                    record.cell,
                    json.dumps(payload, sort_keys=True),
                    canonical_digest(payload),
                ),
            )
            self._checkpoint("cell_written")

    def missing(self):
        done = {(r[0], r[1]) for r in self.connection.execute("SELECT task_id,cell FROM cells")}
        return [
            (task, cell)
            for task in self.binding.assigned_ids
            for cell in CELL_PURPOSES
            if (task, cell) not in done
        ]

    def restore(self, spec, *, ledger, retained_result_reader=None):
        request_set = self._check_spec(spec)
        task = self.connection.execute(
            "SELECT request_set_sha FROM tasks WHERE task_id=?", (spec.item.prompt_id,)
        ).fetchone()
        if task and task[0] != request_set:
            raise CampaignError("campaign_requests_changed")
        restored = {}
        for row in self.connection.execute(
            "SELECT record_json,record_sha FROM cells WHERE task_id=?", (spec.item.prompt_id,)
        ):
            payload = json.loads(row[0])
            if canonical_digest(payload) != row[1]:
                raise CampaignError("campaign_record_changed")
            record = StoredCell.model_validate(payload)
            expected = next(c for c in spec.cells if c.name == record.cell)
            if (
                record.task_id != spec.item.prompt_id
                or record.context.task_id != spec.item.prompt_id
                or record.context.semantic_context() != expected.context.semantic_context()
                or record.request_identity_sha256 != expected.prepared.request_identity_sha256
            ):
                raise CampaignError("campaign_owner_scope")
            # Actual original scope is checked before invoking the private result reader.
            counters = ledger.child_counters(record.allocation, context=record.context)
            expected_state = (
                "complete" if record.capture_sha256 is not None else "cancelled_before_dispatch"
            )
            if (
                counters.mode != "offline"
                or counters.used_attempts != 0
                or counters.remaining_attempts != 0
                or counters.child_state != expected_state
                or ledger.authority.max_outbound_attempts != 0
            ):
                raise CampaignError("campaign_owner_scope")
            if record.result_sha256 is not None:
                if retained_result_reader is None:
                    raise CampaignError("retained_result_reader_required")
                result = retained_result_reader(record.result_sha256)
                if (
                    not isinstance(result, Result)
                    or canonical_digest(result.model_dump(mode="json")) != record.result_sha256
                ):
                    raise CampaignError("retained_result_changed")
                result = Result.model_validate(result.model_dump(mode="json"))
                if self.binding.evidence_status == "frozen_corpus" and result.origin != "replay":
                    raise CampaignError("synthetic_source_not_benchmark")
                capture = (
                    None if result.source_capture is None else result.source_capture.capture_sha256
                )
                if capture != record.capture_sha256:
                    raise CampaignError("retained_capture_changed")
            else:
                result = None
            value = CellObservation(
                record.cell,
                record.span_id,
                record.request_identity_sha256,
                result,
                record.reason,
                record.allocation,
                record.context,
                True,
            )
            validate_retained_observation(spec, value, ledger)
            restored[record.cell] = value
        return restored

    def collect(
        self,
        spec,
        *,
        ledger,
        budget,
        records,
        retained_result_reader=None,
        retained_result_writer=None,
        verified_live_receipts=None,
        clock=None,
    ):
        records = tuple(records)
        if capture_book_digest(records) != self.binding.source_book_sha256:
            raise CampaignError("campaign_source_book_changed")
        if self.binding.evidence_status == "frozen_corpus" and any(
            r.source.origin != "live" for r in records
        ):
            raise CampaignError("synthetic_source_not_benchmark")
        if self.binding.split == "heldout":
            exposure = self.connection.execute(
                "SELECT admission_json,admission_sha FROM exposure"
            ).fetchone()
            if exposure is None:
                raise CampaignError("heldout_admission_required")
            payload = json.loads(exposure[0])
            b = self.binding
            expected = dict(
                manifest_sha256=b.manifest_sha256,
                independent_seal_sha256=b.independent_seal_sha256,
                dev_config_sha256=b.dev_config_sha256,
                method_config_sha256=b.method_config_sha256,
                candidate_commit=b.source_revision,
                history_ref=b.history_ref,
                rerun_reason=None,
            )
            if canonical_digest(payload) != exposure[1] or payload != expected:
                raise CampaignError("heldout_frozen_admission_changed")
        self._admit_task(spec)
        restored = self.restore(spec, ledger=ledger, retained_result_reader=retained_result_reader)
        if len(restored) == len(CELL_PURPOSES):
            ordered = tuple(restored[n] for n in CELL_PURPOSES)
            return ObservationMatrix(spec, ordered[0].allocation, ordered)
        rebound = build_matrix_spec(
            spec.item,
            config=spec.config,
            authority=budget,
            limits=spec.limits,
            run_id=spec.root_context.run_id,
            operation_id="collection-" + uuid4().hex,
            cheap_text=spec.expected_cheap_text,
        )

        def captured(value):
            if retained_result_writer is not None and value.result is not None:
                retained_result_writer(
                    canonical_digest(value.result.model_dump(mode="json")),
                    Result.model_validate(value.result.model_dump(mode="json")),
                )
            self.record_cell(rebound, value, ledger=ledger)

        return collect_matrix(
            rebound,
            ledger=ledger,
            budget=budget,
            fixtures=FixtureIndex(records, verified_live_receipts=verified_live_receipts),
            clock=clock,
            completed_cells=restored,
            on_cell=captured,
            mode=self.binding.mode,
        )
