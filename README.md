# LLM Router Eval

This project will compare routing policies on a fixed prompt set using quality, provider-reported cost and latency. It will also export reusable accounting, provider and routing modules.

Current implementation: **offline provider, data, routing and anchored scoring foundations**. These include SQLite/Decimal accounting, strict fixture lookup, family splits/seals, seven prompt-only routing variants and deterministic scoring. Live SDK/HTTP transport, canonical-statistics/rubric integration and a complete evaluation runner remain pending.

## Quickstart

With Python 3.12 and uv:

```sh
uv sync --frozen
uv run llm-router-eval status
```

The status command uses no network or API keys:

```json
{"application": "llm-router-eval", "data": "local_admission_controls_corpus_pending", "evaluation": "not_implemented", "implementation": "offline_scoring_foundation", "network": "not_required", "routing": "seven_variants_caller_callbacks", "scoring": "anchored_deterministic_rubric_unvalidated", "shared_modules": "offline_controller_available_live_transport_not_implemented", "version": "0.0.1"}
```

## Architecture

A local Python CLI will collect responses, replay routing policies, score results and generate a quality-versus-cost chart. The current SQLite ledger owns durable accounting events; JSONL is a derived export. See the [public interface](docs/INTERFACE.md) and [ledger/pricing contract](src/llm_router_eval/shared/callledger/README.md) for validation, evidence and copy boundaries.

## Results and cost

No evaluation has run. Model quality, cost, latency and savings are **not yet measured**. Prices, accounting scenarios and twelve provider foundation cases are explicitly synthetic controls, not independent heldout Gold or real provider captures. See the [provider contract](docs/provider-contracts.md) for strict replay, zero-dispatch and source/current provenance semantics.

The [data contract](docs/data-contracts.md) separates routing inputs from private scoring data and describes exact-source admission, deterministic family splits and configuration seals. Ten tiny authored data controls exercise this plumbing. A licensed corpus, independent heldout Gold and human calibration remain pending.

The [routing contract](docs/routing-contracts.md) covers the two fixed baselines, rules, classifier, Jev gate and both cascade checkers. Cascades escalate every cheap answer without trusted matching calibration. Current tests use authored callback results; no real calibration or quality claim is implied.

The [scoring contract](docs/scoring-contracts.md) describes anchored numeric/MC/code/exact scoring, visible assigned failures/unscored denominators, blank label templates and calibration domain controls. Generic statistics await verified canonical S2 code; no duplicate statistics library or real human/judge calibration is included.

## Limits and verification

See [limitations](docs/LIMITATIONS.md) and [Bolt handoff](BOLT-HANDOFF.md). The package is a local tool; importing it into a website builder does not run Python evaluation.

Coding agents wrote this implementation. Synthetic offline tests cover actual controller-to-ledger calls, zero-quota fixture hits/misses, typed/structured contracts, caller-limit races/restarts, legacy-store migration, process-crash boundaries, pricing, bounded PNG buffers and privacy controls. They do not exercise a live provider or establish actual price/token proofs. Remote CI has not run; local evidence does not establish finished-app acceptance.

## Development

```sh
uv run ruff check .
uv run ruff format --check .
uv run pytest -m "not live"
```

The repository is standalone. Agent coordination, plans, receipts and transcripts stay in the outer development shell.
