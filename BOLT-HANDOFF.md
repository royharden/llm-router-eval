# Bolt handoff

Status: local Python evaluation core with an unfinished CLI/report workflow. Development is paused; see [development status](DEVELOPMENT-STATUS.md). This is an inspection and portability handoff, not a provisioning or hosting acceptance record.

| Resource | Actual requirement |
|---|---|
| Runtime | Python 3.12 with locked dependencies managed by uv |
| Local accounting/campaign state | Explicit local SQLite files; no hosted database required |
| Database migrations, edge functions, auth, buckets, Realtime | Not required by the current local core |
| Provider credentials in Bolt | Not required by the keyless authored controls; live transport is unimplemented |
| Browser frontend | Optional later safe-result viewer; not implemented |
| Safe result assets | JSON/Markdown prototype in uncommitted WIP, not a verified finished export workflow |
| Scientific charts | Not rendered; plotting dependency/runtime verification pending |
| Cloud provisioning, remote import, publication | Not performed or established |

From the repository, `uv sync --frozen` prepares the locked environment and `uv run llm-router-eval status` inspects local capabilities without provider keys. The provisional explicit-input evaluation commands are described as WIP in the development status; do not treat them as a completed hosted application.

The MVP is a local CLI/evaluation core. There is no forced web frontend or cloud database: a Bolt static preview cannot execute its Python worker or durable local SQLite accounting. This is an explicit portability boundary. A later viewer could consume allowlisted safe aggregate assets after the export workflow is verified. No provider key belongs in frontend code.

If preparing a future Bolt import, keep the local Python core separate and request only a static safe-artifact viewer when actual assets exist. Provision no cloud database, worker, secrets or functions from this document. Real model execution, corpus/reference admission, independent review and publication retain their separate prerequisites.
