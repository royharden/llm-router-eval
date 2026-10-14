# LLM Router Eval

This project will compare routing policies on a fixed prompt set using quality, provider-reported cost and latency. It will also export reusable accounting, provider and routing modules.

Current implementation: **WP02 portable wire models, SQLite ledger and Decimal pricing foundation**. Provider/controller execution and routing evaluation are not implemented yet.

## Quickstart

With Python 3.12 and uv:

```sh
uv sync --frozen
uv run llm-router-eval status
```

The status command uses no network or API keys:

```json
{"application": "llm-router-eval", "evaluation": "not_implemented", "implementation": "ledger_and_pricing_foundation", "network": "not_required", "shared_modules": "ledger_pricing_available_provider_execution_not_implemented", "version": "0.0.1"}
```

## Architecture

A local Python CLI will collect responses, replay routing policies, score results and generate a quality-versus-cost chart. The current SQLite ledger owns durable accounting events; JSONL is a derived export. See the [public interface](docs/INTERFACE.md) and [ledger/pricing contract](src/llm_router_eval/shared/callledger/README.md) for validation, evidence and copy boundaries.

## Results and cost

No evaluation has run. Model quality, cost, latency and savings are **not yet measured**. Committed prices and accounting scenarios are explicitly synthetic test fixtures.

## Limits and verification

See [limitations](docs/LIMITATIONS.md) and [Bolt handoff](BOLT-HANDOFF.md). The package is a local tool; importing it into a website builder does not run Python evaluation.

Coding agents wrote this implementation. Synthetic offline tests cover typed wire rejection, atomic whole-operation funding, conserved holds, persistent counters/unknown liabilities, actual process-crash commit boundaries, concurrent admission, torn projection recovery, Decimal billing, privacy controls and detached imports. They do not exercise provider transport or actual price/token proofs. Remote CI has not run; local contract evidence does not establish finished-app acceptance.

## Development

```sh
uv run ruff check .
uv run ruff format --check .
uv run pytest -m "not live"
```

The repository is standalone. Agent coordination, plans, receipts and transcripts stay in the outer development shell.
