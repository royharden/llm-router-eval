# Bolt handoff

The implemented runtime is a **local Python 3.12 CLI**, with SQLite accounting and allowlisted JSON/Markdown exports. It is suitable for local authored, zero-dispatch demonstrations. This is not hosted execution or finished-app acceptance.

| Resource | Actual requirement |
|---|---|
| Runtime/dependencies | Python 3.12, uv lock, Pydantic and pinned Matplotlib; no SDK is needed for the authored path |
| Local state | Explicit new output directory with SQLite accounting; optional campaign metadata remains caller-scoped |
| Hosted database/migrations/edge functions/auth/buckets/Realtime | Not required by this local core |
| Provider credentials | Explicit caller-injected project credential for optional SDK transport; authored controls use none |
| Browser frontend | Optional later static safe-result viewer; not implemented |
| Safe assets | Aggregate `report.json` and `report.md`, optional PNG/SVG and safe `figure-data.json`; no raw payload archive |
| Scientific charts | Authored `--plots` PNG/SVG with canonical family intervals; real-model study remains pending |
| Cloud provisioning/remote import/publication | Not performed or authorized by this document |

Use the exact local normal/refusal/demo commands in [README](README.md). Reports retain synthetic provenance, unscored rubric coverage, unknown cost and local-only timing limitations. A simulated funding refusal is not a paid model response.

MVP-first portability boundary: a Bolt static preview cannot run the Python worker or durable SQLite accounting. Keep the CLI local; do not add an unnecessary hosted database or Python service to claim compatibility. A future static viewer can consume only verified safe aggregates. No provider key belongs in frontend code.

Future Bolt preparation prompt, when an actual viewer is requested: “Create a static viewer for supplied allowlisted routing aggregate JSON. Preserve the synthetic watermark, assigned/eligible/unscored coverage, unknown costs and local-only timing labels. Do not provision a database, Python worker, functions, auth or provider secrets. Do not infer model quality or live execution from fixture data.” No provisioning is performed by this handoff.

See [development status](DEVELOPMENT-STATUS.md) for missing integration/evaluation and independent review gates. The earlier isolated SDK setup/probe failures remain failures. The optional [native producer](docs/native-sdk.md) and its unknown-usage repair have independent bounded acceptance for mocked SDK accounting and Windows worker closure. Each consumer still requires its actual pinned-source accounting join; real account/pricing integration remains pending.
