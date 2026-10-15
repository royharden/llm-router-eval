# LLM Router Eval

This project will compare routing policies on a fixed prompt set using quality, provider-reported cost and latency. It will also export reusable accounting, provider and routing modules.

Current committed implementation includes offline provider/accounting, local data admission, seven routing policies, deterministic scoring, scope-bound campaign recovery and private adapters to the canonical fixed-population comparison engine. A subsequent CLI/report workflow is **unfinished WIP**. Development is paused; see [development status](DEVELOPMENT-STATUS.md) for completed work, outstanding tasks and [lessons](LESSONS.md).

## Quickstart

With Python 3.12 and uv:

```sh
uv sync --frozen
uv run llm-router-eval status
```

The status command uses no network or API keys. The working tree contains provisional `eval`/`demo` commands, but their first focused workflow run had one failing malformed-response control. They are not a completed CLI release; exact evaluated command examples will follow successful verification.

## Architecture

A local Python CLI will collect responses, replay routing policies, score results and generate a quality-versus-cost chart. The current SQLite ledger owns durable accounting events; JSONL is a derived export. See the [public interface](docs/INTERFACE.md) and [ledger/pricing contract](src/llm_router_eval/shared/callledger/README.md) for validation, evidence and copy boundaries.

## Results and cost

Authored offline controls have executed through the real local Provider/Ledger and canonical comparison paths. Model quality, real cost, inference latency and savings are **not measured**. Prices, accounting scenarios and twelve provider foundation cases are explicitly synthetic controls, not independent heldout Gold or real provider captures. See the [provider contract](docs/provider-contracts.md) for strict replay, zero-dispatch and source/current provenance semantics.

The [data contract](docs/data-contracts.md) separates routing inputs from private scoring data and describes exact-source admission, deterministic family splits and configuration seals. Ten tiny authored data controls exercise this plumbing. A licensed corpus, independent heldout Gold and human calibration remain pending.

The [routing contract](docs/routing-contracts.md) covers the two fixed baselines, rules, classifier, Jev gate and both cascade checkers. Cascades escalate every cheap answer without trusted matching calibration. Current tests use authored callback results; no real calibration or quality claim is implied.

The [scoring contract](docs/scoring-contracts.md) describes anchored numeric/MC/code/exact scoring, visible assigned failures/unscored denominators, blank label templates and calibration domain controls. The canonical statistics package and adapter are implemented; real human/judge calibration and rubric quality remain unvalidated.

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
