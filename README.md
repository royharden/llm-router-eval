# LLM Router Eval

Run a local comparison of seven routing policies on ten authored controls, with SQLite accounting and aggregate JSON/Markdown reports. The default path makes zero live model calls. Real model quality, provider prices, inference latency and savings remain unmeasured.

## Run locally

With Python 3.12 and uv, prepare the locked environment and inspect capabilities:

```sh
uv sync --frozen
uv run --frozen python -m llm_router_eval status
```

From the repository, run the committed authored task/vector inputs into a **new** output directory whose parent exists:

```sh
uv run --frozen python -m llm_router_eval eval --tasks fixtures/implementation-controls/tasks.json --vectors fixtures/implementation-controls/requests.json --out ../router-demo-normal-001
```

The output contains `accounting.sqlite3`, `report.json` and `report.md`. All seven policies run against one six-cell matrix per input. Eight deterministic cases are eligible; two rubric cases remain unscored. Reports distinguish the actual external charge of zero from invented source-cost controls. Those values are not real model prices or quality results.

Add `--plots` to the normal or demo command to export `quality-cost.png`, `quality-cost.svg` and `figure-data.json`. The combined figure pairs the same deterministic cost/quality population with a separate local-computation timing panel. Canonical 95% family intervals use 2,000 shared draws and require at least two families; real model quality and latency remain unmeasured. Unknown costs omit points while preserving their lower/unknown-upper values in the exact table. See [plotting and reproducibility](docs/plotting.md).

Reusing the same output path returns exit code 2 and a closed refusal, preserving the existing files. Input validation also refuses before output creation. No implicit dataset source, overwrite, payload archive, provider credentials or network is required.

## Error and demonstration paths

Use a different fresh output path for every command:

```sh
uv run --frozen python -m llm_router_eval demo --tasks fixtures/implementation-controls/tasks.json --vectors fixtures/implementation-controls/requests.json --out ../router-demo-malformed-001 --scenario malformed
uv run --frozen python -m llm_router_eval demo --tasks fixtures/implementation-controls/tasks.json --vectors fixtures/implementation-controls/requests.json --out ../router-demo-budget-001 --scenario budget
uv run --frozen python -m llm_router_eval demo --tasks fixtures/implementation-controls/tasks.json --vectors fixtures/implementation-controls/requests.json --out ../router-demo-unknown-001 --scenario unknown_cost
```

`malformed` decodes an authored invalid structured response through the actual local adapter, retains failed capture status and falls back to frontier fixtures without a hidden call. `budget` adds an actual ledger rejection using explicitly synthetic prices in simulation mode; it creates no paid provider Result. `unknown_cost` preserves null source-cost points and upper bounds, while actual external charge remains zero.

`code`, `cheap` and `escalation` name observations of the same complete authored matrix: bounded local code executes, cheap answers are selected, and unvalidated cascade checkers escalate. These are construction demonstrations, not separately sampled model experiments.

## Accounting, privacy and interpretation

The SQLite/Decimal ledger owns funding and durable accounting. Policies use fresh public contexts and reuse the declared observations. Canonical fixed-population comparison retains missing/stale cases and explicitly not-run methods. Private scoring labels/rubrics do not enter routing requests or safe reports. Frozen population bytes and method configuration identities are checked before execution.

Reports show assigned/eligible/unscored coverage, source status counts, collection completeness, separate collection and counterfactual consumption costs, exact cost-per-pass ratios and local lookup/policy timing counts with p50/p95. The deterministic cohort pairs accuracy and source consumption on the same eight eligible cases. Separate GSM8K, MMLU, code, exact and rubric aggregates report assigned/eligible/family counts without raw identifiers; absent kinds and an empty deterministic cohort remain explicit. Cohort source costs cover model observations only: code-only execution can consume zero model sources while host-resource cost remains unknown. Incomplete or unknown source cost has no invented total or upper bound. Zero passes leaves cost-per-pass undefined. Local fixture lookup timing is not model latency or TTFT; a new execution produces new timing input. Re-rendering the same retained report is byte deterministic.

Report-only references use the deterministic cohort and already retained observations. A fixed seed (`20261014`) and three declared code/cheap/frontier integer-weight rows (`1/1/1`, `1/2/1`, `1/1/2`) draw only from publicly eligible tiers. Every grid row is shown; none is selected as a tuned winner. The cheapest-passing oracle uses completed private scoring only in reporting. If no tier passes it selects the least-cost eligible tier and remains failed; missing observations or an unknown cost rank produce explicit unavailable cases in the assigned denominator. Reference costs are counterfactual model-source costs, with unknown host cost and unmeasured timing. These references are not runnable routing policies.

See [evaluation contracts](docs/evaluation-contracts.md), [provider contracts](docs/provider-contracts.md), [data contracts](docs/data-contracts.md), [routing](docs/routing-contracts.md) and [scoring](docs/scoring-contracts.md). Reusable modules are under `src/llm_router_eval/shared`; the canonical vendored statistics engine retains its licence and source provenance.

## Remaining work and verification

See [development status](DEVELOPMENT-STATUS.md), [limitations](docs/LIMITATIONS.md), [lessons](LESSONS.md) and [Bolt handoff](BOLT-HANDOFF.md). Real SDK transport, licensed corpus inputs, independent labels/calibration, controlled heldout evaluation and model-quality conclusions remain outstanding. Optional viewer and telemetry emitter are deferred. Independent finished-app review and release acceptance are separate from local checks.

```sh
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen pytest -m "not live"
```

The repository runs standalone. Coordination, transcripts, plans and verification receipts stay outside the product repository. No cloud resources, remote CI, publication or provisioning are claimed.
