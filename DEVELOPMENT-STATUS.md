# Development status — paused 2026-10-15

Development is paused. The last completed source checkpoint provides private domain references and canonical fixed-population comparison through the actual offline Provider/Ledger path. The subsequent local CLI/report workflow is unfinished working-tree material and is preserved for resumption.

## Finished

- Strict portable wire models and 18 generated schemas, SQLite/Decimal funding and accounting, caller-scope checks, restart/crash boundaries, and zero-dispatch fixture reuse.
- Offline provider/request preparation and response contracts, bounded generation-repair controls, exact source capture identities and explicit source/current cost separation. Live provider transport is not implemented.
- Local dataset admission, family splits and configuration seals; ten authored implementation controls. Seven prompt-only routing policies and anchored deterministic scoring are implemented. Unvalidated cascades escalate; rubric judgments remain unscored.
- The canonical statistics/comparison package is vendored with its licence and unchanged runtime source. App adapters use its fixed-ID comparison and metric ports rather than a competing generic statistics engine.
- Six-cell observation matrices are declared before lookup and shared across seven policy executions. Campaign metadata supports scope-bound missing-only recovery with an explicit retained-result reader and canonical heldout-history admission. This does not create real heldout authority.
- Private reference provenance and derived-population byte checks protect scoring labels from routing inputs. Missing, stale and disabled methods remain visible. A collection failure is not silently retried by later policies.
- Last completed source checkpoint: 552 local tests and 16 detached controls passed, including exact Git source relocation. Lint, formatting, all 18 schema bytes and whitespace checks passed. These are source/control checks, not model-quality or finished-app acceptance.

## Worked on, not finished

The following files remain uncommitted WIP:

- `src/llm_router_eval/evaluation/report.py`: safe aggregate JSON/Markdown assembly, canonical metrics and synthetic source-cost summaries.
- `src/llm_router_eval/evaluation/workflow.py`: explicit-input authored DEV workflow, local SQLite output and small keyless scenarios.
- `src/llm_router_eval/cli.py`: provisional `eval` and `demo` commands with explicit task/vector/output paths and refusal of an existing output directory.
- `tests/runner/test_local_workflow.py` and `tests/bootstrap/test_installed_cli.py`: workflow and CLI regression controls.

The first workflow run passed nine controls and failed the malformed structured-response scenario. That scenario provided an invalid successful fixture; collection refused it, so the expected complete source matrix was absent. Preserve the failed result and correct the boundary after resumption. Normal output, no-overwrite refusal, raw-input canary exclusion, unknown source cost and simulated funding rejection passed in that initial focused run. The entire WIP has not passed final regression, formatting or detached verification, and is not a completed CLI deliverable.

Separate isolated SDK dependency setup downloaded/verified the exact packages and completed an offline install. Its bounded guarded source probe timed out. The environment snapshot comparison also reported a transient mismatch; a later read-only comparison matched the starting snapshot, which does not clear the earlier failure. No successful SDK import/capability, client construction, paid request, account access, pricing or transport behavior is established. The app's dependency lock was not changed.

## Outstanding

1. After an explicit resume, correct the malformed-response scenario, finish/report incomplete collection semantics, and verify the existing WIP against a fresh pinned candidate.
2. Finish the smallest runnable local CLI workflow, record an exact normal command and refusal demo, and update the public command examples from actual execution.
3. Complete cost/coverage/timing tables and deterministic report rendering. Preserve unknown cost/usage and separate fixture lookup time from model latency. Chart rendering and optional viewer/telemetry remain pending.
4. Diagnose the source-probe timeout and persist useful before/after environment differences. Reuse verified local artifacts only under a newly authorized bounded action; preserve previous failures. Implement actual SDK transport only with reviewed source behavior and separate execution authority.
5. Acquire actual licensed corpus inputs, independent reference labels, human calibration anchors and heldout seals/history authority. None exists merely because structural 300-row trust doubles pass.
6. Real model testing, benchmark quality/cost/latency conclusions and independent finished-app review require their actual prerequisites. There is no production, hosting, remote CI or release acceptance claim.

No server, Docker stack, cloud resources, hosted database, migrations or edge functions are needed for the current local Python core. See [Bolt handoff](BOLT-HANDOFF.md), [limitations](docs/LIMITATIONS.md) and [lessons](LESSONS.md).
