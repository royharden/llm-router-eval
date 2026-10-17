# Offline matrix execution

The app-owned `evaluation` package assembles existing shared components. `PublicInferenceConfig` contains model/provider declarations and the actual request-template digest. Public routing inputs, configuration and templates determine source generation and request keys. Dataset digests, labels, rubric and difficulty are excluded. Private scoring follows collection.

`build_control_book` reads the existing explicitly synthetic request-vector envelope and verifies each public prompt. Authored answer strings supply separate synthetic fixture outcomes. Those strings are not read from runtime scoring labels, and the original vectors are not presented as recorded model calls. Every capture remains synthetic. Declared source costs are invented counterfactual controls; normalized source usage and source timing remain unknown. Current external charge is zero.

The matrix declares cheap, frontier, classifier, Jev gate and both checker children in one actual OperationPlan before the first lookup. All children and the authority have zero outbound quotas. The existing Provider performs strict fixture lookup and actual CallLedger reuse accounting; no transport parameter or SDK exists in this path. Root purpose differs from declared child purposes and is checked by the owner implementation. Checker request payloads are prebuilt from the retained cheap fixture response, then matched to the actual cheap observation. An unavailable cheap observation leaves checker cells explicitly not run; a different usable response refuses those checker requests and closes this operation's remaining undispatched children.

Policies receive a fresh public RouteContext. Generated callback requests must match both the expected cell purpose and exact prepared request bytes. Each policy consumes every router/check/answer observation at most once. A cascade's cached cheap answer is not consumed twice. Default unvalidated checkers escalate honestly. Code uses the actual bounded grammar, emits a distinct non-model span for each actual execution and invents no model token usage or host-resource price. The runner records actual lookup spans; lookup timing is not model inference latency or TTFT.

Full request/results remain transient. `safe_projection` allowlists closed IDs, statuses, source/current costs and timing metadata; it excludes prompts, responses, private labels, raw usage and error bodies. A new actual execution can have new span/timing data. Deterministic report rendering must instead use a fixed retained metadata input. This module supplies no implicit raw-payload persistence or resume store. A later campaign adapter must require an explicitly supplied retained-result reader, bind exact durable result/capture digests and actual owner allocation/counters, and execute only missing cells.

## Durable campaign state

`CampaignStore` is separate app-owned SQLite metadata. A trusted caller supplies its exact authority map separately from data. The grant binds the fixed experiment seal, actual capture-book digest, public model configuration, assigned IDs, seven method configurations, state ID and resolved database location. Reopening with changed inputs or a different location refuses; completed cells are unique and cannot be overwritten. A synthetic helper authorizes only authored DEV controls. A real corpus or heldout flag cannot provide its own authority.

Concurrent initializers use a bounded standard-library OS file lock. Its handle releases on process exit, without PID guessing or stale-lock deletion; the adjacent lock file contains no payload. The explicit trusted new-store grant permits an empty allocation left before SQLite wrote its first header. Nonempty unrelated files are preserved. Ordinary metadata transactions and duplicate-cell exclusion remain SQLite-authoritative.

Each committed cell stores the exact result/capture/request digests and original actual owner allocation/context. The database omits raw prompts, responses, schema/usage/error bodies and labels. Resumption requires an explicitly supplied retained-result reader. The original canonical owner scope/counters are checked before the reader; returned bytes, schema and provenance must match the durable record. Full completed matrices do no new provider lookup. Partial matrices execute only metadata-missing cells under a new full six-child operation; old cells preserve original source attribution, and restored lookup timing is identified as retained. Never-run checker cells have no fabricated span or timing.

An optional explicit result writer receives a copy of actual results. No default archive is created. Archive writing, the shared accounting ledger and campaign metadata are separate durability domains: a crash after an actual lookup but before its cell metadata commits may require another local replay read. That extra read remains in the owner ledger, has no fresh-model claim or current external charge, and does not reset counters or source liabilities. The campaign neither reserves nor settles money. Each actual local code execution receives its own non-model span.

The store implements the canonical S2 `HeldoutHistory` port. An actual heldout owner grant must bind independent seal, dev lock, method configurations, manifest, source revision and history reference before exposure. The admission is persisted once; a repeated call or a reason string does not grant another exposure. Captured synthetic source cannot be used as real benchmark evidence. The location/state binding does not claim tamper resistance against an owner deleting or replacing the physical store; real authority, independent seal and external history custody remain required.

The authored CLI and aggregate figures are implemented. The separate recorded-replay entrypoint described below connects existing admission and campaign ports; actual licensed corpus inputs, independent labels/calibration, heldout authority, reviewed recordings and finished-app acceptance retain their separate prerequisites.

## Canonical comparison and private references

The app's `domain` adapter creates canonical S2 GoldRecords and an exact fixed manifest from admitted local bytes. Private answer/rubric labels stay with the scoring adapter. Public method inputs contain only `task_id` and `prompt`. The derived in-memory JSONL population has its actual byte digest in the manifest; every comparison rechecks that digest before dispatch, including nested label mutation. These are derived memory references, not claims that a corpus file was written.

Synthetic controls always retain synthetic reference origin and no human provenance. A real frozen population needs the existing exact DatasetAdmission, plus a separately trusted reference receipt binding every task's explicit label source and provenance to those exact dataset bytes. A dataset flag cannot declare human labels. Structural tests use explicitly synthetic 300-row trust doubles; no real source rights, independent Gold or heldout evaluation are created by those tests. Independent heldout seal, dev lock and canonical history admission are still required before heldout exposure.

`engine.run_comparison` calls the unchanged vendored S2 `runner.compare`. Seven matrix-backed MethodRunner ports share one predeclared six-cell collection per input and receive fresh policy contexts. Source collection failures are retained for that coordinator, so later methods cannot silently trigger a retry. Method configuration identities include public inference configuration, policy parameters and explicitly trusted calibration references, without labels. A changed method catalog refuses before source execution.

S2 retains all frozen IDs across missing and stale source statuses. Disabled methods are explicitly not run, with no fabricated predictions or observed calls. Failed deterministic cases count as failures; rubric cases remain unlabelled and unscored without validated judging evidence. Synthetic predictions remain synthetic, and local code execution has no invented model identity. Private comparisons and transient source captures are not automatic public reports or payload archives. Aggregate reporting and the CLI are subsequent app assembly work; this adapter checkpoint does not establish benchmark quality or release readiness.

## Authored CLI and safe reports

`eval` requires explicit task/vector inputs and a fresh output directory. It admits only small synthetic DEV inputs with zero outbound authority; it does not accept its own flags as frozen/heldout authority. `demo` adds named authored scenarios. Malformed structured wire is decoded by the existing adapter into failed captures, then consumed through the actual mock Provider/Ledger path. The funding-refusal scenario is a distinct synthetic simulation-mode ledger proof, not a paid provider Result.

Aggregate reporting calls the unchanged canonical metric/accuracy/not-measured ports. It retains eligible versus unscored rubric counts and missing/stale/error statuses. Source costs are observed fixture provenance or explicitly invented counterfactual controls; they are separate from current external charge. A missing matrix or unknown observed source cost leaves the source point/upper total unknown. Reported known subtotals are not claims about unretained cells. Cost-per-pass is represented as a cost/pass-count ratio and is undefined at zero passes. Local lookup/policy timing uses standard-library inclusive quantiles with counts, without a model-latency or TTFT claim.

No raw labels, prompts, answers, arbitrary provider errors or usage bodies enter the constructed safe reports. Report serialization is deterministic from the same report data; a new execution records different timing input. Existing outputs are refused before ledger/report writes. A failed execution can retain its own partial new directory; no overwrite or implicit cleanup is performed.

## Explicit recorded-replay CLI

`replay --dataset <file> --replay <file> --admission <file>
--admission-sha256 <expected digest> --split dev|heldout --out <new directory>`
has a separate path from `eval`/`demo`. Their synthetic-only/twelve-control limits
are unchanged. No network client, provider credential, transport, acquisition or
paid dispatch is accepted by this workflow. It invokes the existing
`MatrixSource(mode="strict_replay")`, `CampaignStore`, canonical comparison and
aggregate report; it does not implement another evaluator or money ledger.

The separate admission file and expected SHA-256 come from the actual trusted
caller, not from an untrusted dataset or its capture bundle. The digest pins bytes;
it does not establish rights, human identity, independent authorship or experiment
authority. Caller-owned provenance and history custody must actually exist.
`ReplayInputs` and `ReplayAdmission` in `evaluation/replay_workflow.py` are the
normative closed wire models, using the existing module-owned models underneath.
Duplicate JSON keys, unknown fields and oversized inputs refuse. Dataset, replay
bundle and admission files are bounded to 8 MB, 40 MB and 4 MB respectively.

The replay bundle contains:

- `run_id`, `frozen_at`, public inference `config`, zero-cap/outbound-zero
  `authority` and outbound-zero `limits`, and all seven `policy_configs`.
- Explicit `disabled_methods`; these retain fixed assigned populations and are
  reported as not run.
- `items`, each containing only `public_input: {prompt_id, text}` and retained
  model `cheap_text`. These must match selected public corpus inputs. A missing
  source item stays unavailable in the fixed denominator. Scoring labels never
  enter request preparation.
- `captures`, each with exact `content_base64`, existing `SourceCaptureRef`,
  namespace, source generation, public-context and dispatched-envelope hashes,
  and review receipt identity. The strict path rejects synthetic source origin;
  existing `FixtureIndex` requires matching separately trusted live-capture
  receipts. These are retained-source declarations, not new live calls. Missing
  individual observations produce explicit failed/unavailable cells; malformed
  supplied source bindings refuse. The retained cheap observation must match the
  checker preparation, without fabricating a replacement answer.

The admission supplies exact `input_bundle_sha256`, existing `DatasetAdmission`
and `ReferenceAdmission` plus its trusted reference map, separately verified
`live_capture_receipts`, `ConfigurationSeal`, `CampaignBinding`, `CampaignGrant`
and its trusted map. For heldout it also supplies the canonical
`heldout_admission` fields. It has no self-authorize or synthetic-promotion flag.
The existing frozen-corpus validation still requires the full prescribed
300-item mix and family-separated 150/150 split. Rights, reference origins and
source receipts cannot be inferred merely from those counts.

The configuration seal binds the exact dataset bytes and canonical family split,
public inference configuration, ordered retained price references, seven method
configurations, policy thresholds, explicit unvalidated/no-judge profile,
capture-book digest and raw replay-bundle bytes. `expected_seal` describes these
used identities; calling it creates no trust. The caller-supplied seal must match.
Preparation reuses the existing zero-dispatch request-evidence profile; it does
not establish new paid token/price bounds. Cascades remain unvalidated and rubric
cases unscored in this CLI profile.

Campaign authority must additionally match the selected split and exact assigned
IDs, method/public/source/seal/manifest identities, independent seal/dev lock,
history reference and resolved new `campaign.sqlite3` location. The pure
`validate_campaign_authority` and `validate_exposure` functions are the same
checks used by durable campaign creation/recording. All of these input checks
precede output creation. Canonical heldout comparison records the actual once-only
exposure through that store; a reason string does not authorize a rerun.

The fresh output contains `accounting.sqlite3`, `campaign.sqlite3`, `report.json`
and `report.md`; the campaign lock is metadata only. Safe aggregates omit raw
prompts, responses and labels. Recorded source-cost metadata remains distinct
from current external charge zero; unknown source costs and missing/error
denominators remain visible. Timing describes local replay, not new inference or
TTFT. A completed command is not calibration, model-quality or release acceptance.

Existing or partial output is always refused and preserved. No implicit
resumption, reset, raw-result archive or automatic new-location grant is provided.
The existing lower-level campaign recovery API still requires an explicit trusted
retained-result reader. Location/state binding cannot prevent an owner from
deleting its own store; actual independent history custody remains necessary.
Structural tests contain original fictional UNIT authority/capture objects and
agent-provisional labels only. Their contract-shaped origin fields do not make
those controls real recordings, source rights, human labels or heldout evidence.
