"""App matrix-backed method ports; fixed-ID dispatch/scoring belongs to canonical S2."""

import time
from dataclasses import dataclass, field
from types import MappingProxyType

from ..shared.callledger.models import canonical_digest
from ..shared.evalkit.models import Prediction, SourceRef
from ..shared.evalkit.runner import Comparison, MethodConfig, SourceView, compare
from ..shared.providers import FixtureIndex
from ..shared.routing.context import RouteInput
from ..shared.routing.policies import Policy
from .campaign import capture_book_digest
from .domain import AnswerAdapter, AnswerPayload, EvaluationView
from .matrix import CELL_PURPOSES, collect_matrix


def policy_configuration_sha(
    policy, public_config_sha256, *, trusted_calibration_receipts=frozenset()
):
    return canonical_digest(
        dict(
            public_config_sha256=public_config_sha256,
            name=policy.name,
            version=policy.version,
            parameters=policy.config.model_dump(mode="json"),
            calibration=None
            if policy.calibration is None
            else policy.calibration.model_dump(mode="json"),
            trusted_calibration_refs=sorted(trusted_calibration_receipts),
        )
    )


class MatrixSource:
    """Public specs, admitted captures and actual accounting only; no scorer/Gold/labels field."""

    def __init__(
        self,
        *,
        specs,
        records,
        ledger,
        budget,
        mode="mock",
        campaign=None,
        retained_result_reader=None,
        retained_result_writer=None,
        verified_live_receipts=None,
        trusted_calibration_receipts=frozenset(),
        clock=None,
    ):
        specs = tuple(specs)
        records = tuple(records)
        if len({s.item.prompt_id for s in specs}) != len(specs):
            raise ValueError("evaluation_source_ids")
        if len({s.config.sha256 for s in specs}) > 1:
            raise ValueError("evaluation_source_config")
        if budget.max_outbound_attempts != 0 or ledger.authority.max_outbound_attempts != 0:
            raise ValueError("evaluation_zero_quota")
        origins = {r.source.origin for r in records}
        if not records or len(origins) != 1 or mode not in {"mock", "strict_replay"}:
            raise ValueError("evaluation_source_origin")
        if mode == "mock" and origins != {"synthetic"}:
            raise ValueError("evaluation_source_origin")
        self.origin = "synthetic" if origins == {"synthetic"} else "recorded_replay"
        self.specs = MappingProxyType({s.item.prompt_id: s for s in specs})
        self.records = records
        self.ledger = ledger
        self.budget = budget
        self.mode = mode
        self.campaign = campaign
        self.reader, self.writer = retained_result_reader, retained_result_writer
        self.live_receipts = verified_live_receipts
        self.fixtures = FixtureIndex(records, verified_live_receipts=verified_live_receipts)
        self.trusted_calibration_receipts = frozenset(trusted_calibration_receipts)
        self.clock = clock or time.monotonic
        self._matrices = {}
        self._failed = set()
        self.executions = {}

    @property
    def source_book_sha256(self):
        return capture_book_digest(self.records)

    def lookup(self, gold):
        spec = self.specs.get(gold.item_id)
        if spec is None:
            return SourceView("unavailable", None, {})
        payload = dict(task_id=spec.item.prompt_id, prompt=spec.item.text)
        return SourceView("available", canonical_digest(payload), payload)

    def matrix_for(self, public_input):
        item = RouteInput.from_data_view(public_input.public_payload)
        if (
            item.prompt_id != public_input.item_id
            or canonical_digest(public_input.public_payload) != public_input.input_sha256
        ):
            raise ValueError("evaluation_public_identity")
        spec = self.specs.get(item.prompt_id)
        if spec is None or item != spec.item:
            raise ValueError("evaluation_public_source")
        if item.prompt_id in self._failed:
            raise ValueError("evaluation_source_unavailable")
        if item.prompt_id not in self._matrices:
            try:
                if self.campaign is None:
                    matrix = collect_matrix(
                        spec,
                        ledger=self.ledger,
                        budget=self.budget,
                        fixtures=self.fixtures,
                        mode=self.mode,
                        clock=self.clock,
                    )
                else:
                    if self.campaign.binding.mode != self.mode:
                        raise ValueError("evaluation_campaign_mode")
                    matrix = self.campaign.collect(
                        spec,
                        ledger=self.ledger,
                        budget=self.budget,
                        records=self.records,
                        retained_result_reader=self.reader,
                        retained_result_writer=self.writer,
                        verified_live_receipts=self.live_receipts,
                        clock=self.clock,
                    )
            except Exception:
                self._failed.add(item.prompt_id)
                raise
            self._matrices[item.prompt_id] = matrix
        return self._matrices[item.prompt_id]

    def safe_matrix_rows(self):
        return [
            dict(task_id=task, **row)
            for task, matrix in self._matrices.items()
            for row in matrix.safe_projection()
        ]


class PolicyRunner:
    """Canonical MethodRunner port with public-only source coordinator and no label callback."""

    def __init__(self, source: MatrixSource, policy: Policy):
        self.source, self.policy = source, policy
        configs = {s.config.sha256 for s in source.specs.values()}
        if len(configs) != 1:
            raise ValueError("evaluation_explicit_config")
        self.config_sha256 = policy_configuration_sha(
            policy,
            next(iter(configs)),
            trusted_calibration_receipts=source.trusted_calibration_receipts,
        )

    def predict(self, public_input):
        matrix = self.source.matrix_for(public_input)
        started = self.source.clock()
        execution = matrix.execute(
            self.policy,
            ledger=self.source.ledger,
            clock=self.source.clock,
            trusted_calibration_receipts=self.source.trusted_calibration_receipts,
        )
        local_ms = max((self.source.clock() - started) * 1000, 0)
        self.source.executions[(self.policy.name, public_input.item_id)] = (execution, local_ms)
        model = None
        references = []
        selected = execution.tier
        if selected != "code":
            observed = matrix.cell(selected).result
            if observed is not None:
                model = observed.model_reported
        for name in CELL_PURPOSES:
            cell = matrix.cell(name)
            if (
                cell.span_id in execution.consumed_span_ids
                and cell.result is not None
                and cell.result.source_capture is not None
            ):
                capture = cell.result.source_capture
                references.append(
                    SourceRef(handle="capture:" + capture.capture_id, sha256=capture.capture_sha256)
                )
        payload = AnswerPayload(candidate_status=execution.answer.status, selected_tier=selected)
        return Prediction(
            item_id=public_input.item_id,
            method=self.policy.name,
            method_version=self.policy.version,
            config_sha256=self.config_sha256,
            model=model,
            origin=self.source.origin,
            status="ok" if execution.answer.status == "ok" else "provider_error",
            domain_payload=payload.model_dump(mode="json"),
            machine_output=execution.answer.text,
            human_effective_output=None,
            human_provenance_ref=None,
            issues=execution.reasons,
            ledger_refs=execution.consumed_span_ids,
            source_refs=tuple(references),
        )


@dataclass(frozen=True)
class DomainComparison:
    comparison: Comparison = field(repr=False)
    method_configs: object
    source: MatrixSource = field(repr=False)
    manifest_sha256: str


def run_comparison(
    view: EvaluationView,
    source: MatrixSource,
    policies,
    *,
    split="dev",
    disabled_methods=frozenset(),
    heldout_admission=None,
    heldout_history=None,
):
    policies = tuple(policies)
    if not policies or len({p.name for p in policies}) != len(policies):
        raise ValueError("evaluation_method_catalog")
    if set(disabled_methods) - {p.name for p in policies}:
        raise ValueError("evaluation_disabled_methods")
    methods = {}
    for policy in policies:
        runner = PolicyRunner(source, policy)
        methods[policy.name] = MethodConfig(
            policy.version,
            runner.config_sha256,
            source.origin,
            None if policy.name in disabled_methods else runner,
        )
    expected = canonical_digest({name: config.config_sha256 for name, config in methods.items()})
    if view.manifest.comparator_config.sha256 != expected:
        raise ValueError("evaluation_method_configuration_changed")
    adapter = AnswerAdapter(view.manifest.publication_policy)
    result = compare(
        methods,
        view.manifest,
        split,
        view.split_records(split),
        adapter,
        None,
        source.lookup,
        heldout_admission=heldout_admission,
        heldout_history=heldout_history,
    )
    return DomainComparison(result, MappingProxyType(methods), source, view.manifest_sha256)
