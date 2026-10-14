# Bolt handoff

Status: **local WP02 wire/ledger/pricing foundation / hosting specification only**. No runnable browser app or backend provisioning inventory exists yet.

| Resource | Requirement |
|---|---|
| Hosted database / migrations | Not required for the local CLI |
| Functions / auth / buckets / Realtime | Not required |
| Provider secrets in Bolt | Not required |
| Python evaluation runtime | Local Python 3.12; outside a static web preview |
| Static viewer | Optional later packaging; not implemented |
| Safe result JSON and charts | Planned; not generated yet |
| Remote repository / import / publication | Requires an actual grant; absent |

The local package uses uv sync --frozen and uv run llm-router-eval status. This reports the ledger/pricing foundation stage without keys. It does not execute evaluation. Shared Python declarations and generated JSON Schemas are documented in docs/INTERFACE.md; the accounting core is documented in src/llm_router_eval/shared/callledger/README.md.

A future viewer may import generated safe result assets. Importing this Python repository does not provision a Python worker, SQLite ledger or API credentials. A provisioning prompt will be prepared only after actual implemented resources and readiness evidence exist. Current action: inspection/preparation only.
