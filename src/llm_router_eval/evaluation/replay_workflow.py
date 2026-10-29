"""Explicit, caller-admitted recorded replay. No acquisition, transport or trust minting."""

import base64
import hashlib
from dataclasses import fields
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal, get_args

from pydantic import Field, JsonValue

from ..data.corpus import DatasetAdmission, digest, load_dataset
from ..data.seals import ConfigurationSeal
from ..data.splits import split_families
from ..shared.callledger import Ledger
from ..shared.callledger.models import (
    BudgetAuthority,
    Digest,
    Identifier,
    Limits,
    WireModel,
    canonical_digest,
    decode_wire_json,
)
from ..shared.evalkit.runner import HeldoutAdmission
from ..shared.providers.captures import FixtureIndex, FixtureRecord, LiveCaptureReceipt
from ..shared.providers.models import SourceCaptureRef
from ..shared.routing.context import RouteInput
from ..shared.routing.policies import Policy, PolicyConfig, PolicyName
from .campaign import (
    CampaignBinding,
    CampaignGrant,
    CampaignStore,
    capture_book_digest,
    validate_campaign_authority,
    validate_exposure,
)
from .domain import ReferenceAdmission, prepare_view
from .engine import MatrixSource, policy_configuration_sha, run_comparison
from .matrix import PublicInferenceConfig, build_matrix_spec
from .report import build_report, render_json, render_markdown

NAMES = get_args(PolicyName)


class ReplayItem(WireModel):
    public_input: RouteInput = Field(repr=False)
    cheap_text: Annotated[str, Field(max_length=16000)] | None = Field(repr=False)


class RecordedCapture(WireModel):
    content_base64: Annotated[str, Field(max_length=175000)] = Field(repr=False)
    source: SourceCaptureRef
    namespace: Identifier
    source_generation: Identifier | None
    consumer_context_sha256: Digest
    dispatched_envelope_sha256: Digest
    review_receipt_sha256: Digest

    def record(self):
        content = base64.b64decode(self.content_base64, validate=True)
        if len(content) > 128000 or self.source.origin != "live":
            raise ValueError("recorded_capture_required")
        return FixtureRecord(
            content,
            self.source,
            self.namespace,
            self.source_generation,
            self.consumer_context_sha256,
            self.dispatched_envelope_sha256,
            self.review_receipt_sha256,
        )


class ReplayInputs(WireModel):
    schema_version: Literal[1]
    run_id: Identifier
    frozen_at: str
    config: PublicInferenceConfig
    authority: BudgetAuthority
    limits: Limits
    policy_configs: dict[PolicyName, PolicyConfig]
    disabled_methods: list[PolicyName] = Field(default_factory=list, max_length=7)
    items: Annotated[list[ReplayItem], Field(min_length=1, max_length=300)] = Field(repr=False)
    captures: Annotated[list[RecordedCapture], Field(min_length=1, max_length=1800)] = Field(
        repr=False
    )


class ReplayAdmission(WireModel):
    """A separate trusted caller artifact, pinned externally through the CLI digest.

    The supplied digest proves bytes, not human identity, rights or independence.
    A dataset/bundle must not choose its own admission artifact and expected hash.
    """

    schema_version: Literal[1]
    input_bundle_sha256: Digest
    dataset_admission: DatasetAdmission
    reference_admission: ReferenceAdmission
    trusted_reference_receipts: dict[Digest, Digest]
    live_capture_receipts: dict[Digest, LiveCaptureReceipt]
    seal: ConfigurationSeal
    campaign_binding: CampaignBinding
    campaign_grant: CampaignGrant
    trusted_campaign_grants: dict[Digest, Digest]
    heldout_admission: dict[str, JsonValue] | None


def _read(path, maximum):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > maximum:
        raise ValueError("replay_input_size")
    with path.open("rb") as source:
        data = source.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("replay_input_size")
    return data


def expected_seal(dataset, partition, inputs, records, configs, bundle_sha256):
    """Declare the actual used configuration; this function grants no trust or exposure."""
    return ConfigurationSeal(
        dataset_sha256=hashlib.sha256(dataset).hexdigest(),
        split_sha256=digest(partition.model_dump(mode="json")),
        models_sha256=inputs.config.sha256,
        prices_sha256=canonical_digest([record.source.price_ref for record in records]),
        policies_sha256=canonical_digest(configs),
        thresholds_sha256=canonical_digest(
            {name: value.model_dump(mode="json") for name, value in inputs.policy_configs.items()}
        ),
        judge_sha256=canonical_digest("rubric-unvalidated-no-judge-v1"),
        sources_sha256=capture_book_digest(records),
        input_manifest_sha256=bundle_sha256,
    )


def _prepare(dataset_bytes, bundle_bytes, admission, target, split):
    bundle_sha = hashlib.sha256(bundle_bytes).hexdigest()
    if bundle_sha != admission.input_bundle_sha256:
        raise ValueError("replay_bundle_binding")
    inputs = ReplayInputs.model_validate(
        decode_wire_json(bundle_bytes.decode("utf-8"), max_bytes=40000000)
    )
    if (
        set(inputs.policy_configs) != set(NAMES)
        or len(set(inputs.disabled_methods)) != len(inputs.disabled_methods)
        or Decimal(inputs.authority.cap_usd) != 0
        or inputs.authority.max_outbound_attempts != 0
        or inputs.limits.max_outbound_attempts != 0
    ):
        raise ValueError("replay_zero_dispatch_configuration")
    data = load_dataset(dataset_bytes, admission=admission.dataset_admission)
    if data.evidence_status != "frozen_corpus":
        raise ValueError("admitted_corpus_required")
    records = tuple(value.record() for value in inputs.captures)
    for key, receipt in admission.live_capture_receipts.items():
        if key != receipt.receipt_sha256:
            raise ValueError("capture_receipt_key")
    fixtures = FixtureIndex(records, verified_live_receipts=admission.live_capture_receipts)
    policies = tuple(Policy(name, config=inputs.policy_configs[name]) for name in NAMES)
    configs = {p.name: policy_configuration_sha(p, inputs.config.sha256) for p in policies}
    partition = split_families(data)
    view = prepare_view(
        dataset_bytes,
        frozen_at=inputs.frozen_at,
        method_configs=configs,
        origin="recorded_replay",
        split_manifest=partition,
        dataset_admission=admission.dataset_admission,
        reference_admission=admission.reference_admission,
        trusted_reference_receipts=admission.trusted_reference_receipts,
    )
    seal = expected_seal(dataset_bytes, partition, inputs, records, configs, bundle_sha)
    if admission.seal != seal:
        raise ValueError("replay_seal_binding")
    binding, grant = validate_campaign_authority(
        target / "campaign.sqlite3",
        binding=admission.campaign_binding,
        grant=admission.campaign_grant,
        trusted_grants=admission.trusted_campaign_grants,
    )
    assigned = [value.item_id for value in view.split_records(split)]
    reference = admission.reference_admission
    if (
        binding.split != split
        or binding.evidence_status != "frozen_corpus"
        or binding.mode != "strict_replay"
        or binding.seal_sha256 != seal.identity()
        or binding.source_book_sha256 != seal.sources_sha256
        or binding.public_config_sha256 != inputs.config.sha256
        or binding.assigned_ids != assigned
        or binding.method_config_sha256 != configs
        or binding.manifest_sha256 != view.manifest_sha256
        or binding.independent_seal_sha256 != reference.independent_seal_sha256
        or binding.dev_config_sha256 != reference.dev_config_sha256
        or binding.history_ref != view.manifest.run_history_ref
    ):
        raise ValueError("replay_campaign_binding")
    exposure = None
    if split == "heldout":
        raw = admission.heldout_admission
        if raw is None or set(raw) != {field.name for field in fields(HeldoutAdmission)}:
            raise ValueError("heldout_admission_required")
        exposure = HeldoutAdmission(**raw)
        validate_exposure(binding, grant, exposure)
    elif admission.heldout_admission is not None:
        raise ValueError("unexpected_heldout_admission")
    seen = set()
    specs = []
    for item in inputs.items:
        public = item.public_input
        if (
            public.prompt_id in seen
            or public.prompt_id not in assigned
            or {"task_id": public.prompt_id, "prompt": public.text}
            != view.public_inputs[public.prompt_id]
            or (item.cheap_text is not None and len(item.cheap_text.encode()) > 16000)
        ):
            raise ValueError("replay_public_input_binding")
        seen.add(public.prompt_id)
        specs.append(
            build_matrix_spec(
                public,
                config=inputs.config,
                authority=inputs.authority,
                limits=inputs.limits,
                run_id=inputs.run_id,
                operation_id="matrix-" + canonical_digest([inputs.run_id, public.prompt_id])[:32],
                cheap_text=item.cheap_text,
            )
        )
    expected_keys = {
        (
            cell.prepared.request_identity_sha256,
            cell.context.consumer_namespace,
            cell.context.source_generation,
            cell.context.consumer_context_sha256,
            cell.prepared.dispatched_envelope_sha256,
        )
        for spec in specs
        for cell in spec.cells
    }
    for record in records:
        if (
            record.source.request_identity_sha256,
            record.namespace,
            record.source_generation,
            record.consumer_context_sha256,
            record.dispatched_envelope_sha256,
        ) not in expected_keys:
            raise ValueError("replay_capture_scope")
    for spec in specs:
        # Reject a malformed supplied source/model binding before output creation.
        # An absent per-cell capture remains an explicit unavailable/error outcome.
        for cell in spec.cells:
            fixtures.lookup(cell.prepared, context=cell.context)
        cheap = spec.cells[0]
        found = fixtures.lookup(cheap.prepared, context=cheap.context)
        if found is not None:
            content, _ = found
            if content.status == "ok" and content.text != spec.expected_cheap_text:
                raise ValueError("replay_cheap_text_binding")
    return inputs, records, policies, view, binding, grant, exposure, tuple(specs)


def run_recorded_replay(
    dataset_path, bundle_path, admission_path, admission_sha256, output_dir, *, split="dev"
):
    """Fresh-output replay only; caller supplies actual rights/reference/capture authority.

    No input flag or helper fabricates recorded captures, human labels or permission.
    Partial output and its history remain retained; implicit restart/reset is unsupported.
    """
    target = Path(output_dir)
    if target.exists() or target.is_symlink() or not target.parent.is_dir():
        raise ValueError("output_exists_or_parent_missing")
    if split not in {"dev", "heldout"}:
        raise ValueError("replay_split")
    authority_bytes = _read(admission_path, 4000000)
    if hashlib.sha256(authority_bytes).hexdigest() != admission_sha256:
        raise ValueError("admission_digest")
    admission = ReplayAdmission.model_validate(
        decode_wire_json(authority_bytes.decode("utf-8"), max_bytes=4000000)
    )
    dataset = _read(dataset_path, 8000000)
    bundle = _read(bundle_path, 40000000)
    inputs, records, policies, view, binding, grant, exposure, specs = _prepare(
        dataset, bundle, admission, target, split
    )
    # All input/authority/seal/public-scope checks above precede any output mutation.
    target.mkdir(parents=False, exist_ok=False)
    with (
        Ledger(
            target / "accounting.sqlite3",
            authority=inputs.authority,
            trusted_requests=[cell.evidence for spec in specs for cell in spec.cells],
        ) as ledger,
        CampaignStore(
            target / "campaign.sqlite3",
            binding=binding,
            grant=grant,
            trusted_grants=admission.trusted_campaign_grants,
        ) as campaign,
    ):
        source = MatrixSource(
            specs=specs,
            records=records,
            ledger=ledger,
            budget=inputs.authority,
            mode="strict_replay",
            campaign=campaign,
            verified_live_receipts=admission.live_capture_receipts,
        )
        comparison = run_comparison(
            view,
            source,
            policies,
            split=split,
            disabled_methods=frozenset(inputs.disabled_methods),
            heldout_admission=exposure,
            heldout_history=campaign if split == "heldout" else None,
        )
        report = build_report(comparison, view, ledger.totals())
        report["admission_sha256"] = admission_sha256
        report["configuration_seal_sha256"] = admission.seal.identity()
        (target / "report.json").write_bytes(render_json(report))
        (target / "report.md").write_bytes(render_markdown(report))
    return report
