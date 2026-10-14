# LLM Router Eval

This project will compare routing policies on a fixed prompt set using quality, provider-reported cost and latency. It will also export reusable accounting, provider and routing modules.

Current implementation: **WP00 keyless package scaffold**. Routing evaluation and shared modules are not implemented yet.

## Quickstart

With Python 3.12 and uv:

```sh
uv sync --frozen
uv run llm-router-eval status
```

The status command uses no network or API keys:

```json
{"application": "llm-router-eval", "evaluation": "not_implemented", "implementation": "scaffold", "network": "not_required", "shared_modules": "awaiting_contract_adoption", "version": "0.0.1"}
```

## Architecture

A local Python CLI will collect responses, replay routing policies, score results and generate a quality-versus-cost chart. SQLite will own durable accounting events; JSONL will be an export. Provider, ledger and routing contracts remain pending agreement before their implementation.

## Results and cost

No evaluation has run. Model quality, cost, latency and savings are **not yet measured**. No generated result table or demo recording is available at this scaffold stage.

## Limits and verification

See [limitations](docs/LIMITATIONS.md) and [Bolt handoff](BOLT-HANDOFF.md). The package is a local tool; importing it into a website builder does not run Python evaluation.

Coding agents wrote this scaffold. On Windows with CPython 3.12.13, both focused tests passed: installed-package execution from outside the repository with network disabled, and rejection of an unimplemented evaluation command. Ruff lint and format checks passed. Remote CI has not run. This is scaffold evidence, not finished-app acceptance.

## Development

```sh
uv run ruff check .
uv run ruff format --check .
uv run pytest -m "not live"
```

The repository is standalone. Agent coordination, plans, receipts and transcripts stay in the outer development shell.
