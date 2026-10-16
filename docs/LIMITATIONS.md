# Current limitations

The repository contains a keyless package, wire models/schemas, a SQLite/Decimal ledger, an offline controller and local data-admission/split/seal validation.

- The local Provider/Ledger matrix runner, campaign recovery and canonical fixed-population comparison adapter are implemented. The authored report/CLI workflow now runs locally; malformed wire is recorded as a failed capture and falls back without hidden calls. Live SDK/HTTP transport and validated rubric integration remain pending.
- No licensed 300-prompt corpus or human judge labels have been acquired.
- No cost, quality, savings or latency result is measured.
- The offline foundation is available as a source snapshot; actual consumer adoption requires its own grant, exact Git blob hashes, licences and moved tests.
- No provider authentication, resource provisioning or public release is established.

Future implementations must generate named failures from actual evaluation output and preserve incomplete evidence honestly.

The data module includes ten authored implementation controls and structural admission tests. It has no independent frozen corpus or upstream licensed-row extractor. Admission receipts and source declarations must come from an actual trusted owner. Campaign metadata and the canonical once-heldout history port exist, but actual heldout source/grant/seal authority remains absent. See [data-contracts.md](data-contracts.md).

The routing code tier supports only bounded integer sum/product prompts. Other code grammars are deferred. Real calibration is absent, so executable cascades escalate every cheap answer; test permits are synthetic. Routing/reference tests establish source behavior only, not cost, quality, live robustness or a full evaluation. See [routing-contracts.md](routing-contracts.md).

Rubric scoring remains unvalidated/unscored. Calibration domain counts and toy intervals do not supply a generic statistics implementation, actual source/labels or quality acceptance. Canonical statistics are present; real human anchors and validated rubric judgment remain pending; missing and unscored records retain their assigned denominator. See [scoring-contracts.md](scoring-contracts.md).

See [INTERFACE.md](INTERFACE.md) and the [ledger contract](../src/llm_router_eval/shared/callledger/README.md). Wire declarations alone do not reserve money; the ledger performs atomic accounting with trusted registered evidence. No live price/token bounds, filesystem/general image decoder or arbitrary full JSON Schema validation are established. A charge above its bound is recorded and halts further funding; an incorrect bound can cause actual spend above the cap.

The [provider contract](provider-contracts.md) describes the deliberately bounded strict-schema subset and in-memory RGB/RGBA PNG checks. There is no filesystem image loader/general decoder or paid image-bound proof. Genuine SDK retries/timeouts/cancellation and broader schema support remain separate gates. Authored synthetic fixtures cannot become live observations by changing an origin/nonce flag.

The local authored workflow is construction evidence only. It accepts at most twelve synthetic DEV cases, not an actual benchmark/heldout population. Reports contain aggregate metrics/cost coverage and local timing; authored PNG/SVG figures are available through `--plots`; real-model execution and real measured scientific results remain pending. See [development status](../DEVELOPMENT-STATUS.md) and [lessons](../LESSONS.md). Separate SDK dependency setup completed installation but its bounded source probe timed out; this is not successful SDK integration.
