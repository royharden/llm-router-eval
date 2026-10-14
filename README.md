# LLM Router Eval

This project will compare routing policies on a fixed prompt set using quality, provider-reported cost and latency. It will also export reusable accounting, provider and routing modules.

Current implementation: **WP01 portable wire models and generated schemas**. Provider execution, durable accounting and routing evaluation are not implemented yet.

## Quickstart

With Python 3.12 and uv:

```sh
uv sync --frozen
uv run llm-router-eval status
```

The status command uses no network or API keys:

```json
{"application": "llm-router-eval", "evaluation": "not_implemented", "implementation": "wire_models_and_schemas", "network": "not_required", "shared_modules": "models_only_execution_not_implemented", "version": "0.0.1"}
```

## Architecture

A local Python CLI will collect responses, replay routing policies, score results and generate a quality-versus-cost chart. SQLite will own durable accounting events; JSONL will be an export. The current shared models describe accepted request/result/accounting declarations. See the [public interface](docs/INTERFACE.md) for validation and copy boundaries.

## Results and cost

No evaluation has run. Model quality, cost, latency and savings are **not yet measured**. No generated result table or demo recording is available at this model stage.

## Limits and verification

See [limitations](docs/LIMITATIONS.md) and [Bolt handoff](BOLT-HANDOFF.md). The package is a local tool; importing it into a website builder does not run Python evaluation.

Coding agents wrote this implementation. Synthetic offline tests cover typed wire rejection, declared accounting consistency, public request identity, metadata projection and detached model imports with network disabled. They do not exercise provider transport or durable funding. Remote CI has not run; local contract evidence does not establish finished-app acceptance.

## Development

```sh
uv run ruff check .
uv run ruff format --check .
uv run pytest -m "not live"
```

The repository is standalone. Agent coordination, plans, receipts and transcripts stay in the outer development shell.
