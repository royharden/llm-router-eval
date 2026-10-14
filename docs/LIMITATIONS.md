# Current limitations

The repository contains a keyless package, shared wire models, generated schemas and a SQLite ledger/Decimal pricing foundation.

- Router policies, scoring, provider/controller transport and evaluation are not implemented.
- No licensed 300-prompt corpus or human judge labels have been acquired.
- No cost, quality, savings or latency result is measured.
- Only the wire/ledger/pricing foundation is available as a source snapshot; executing provider interfaces remain unavailable and consumer adoption requires its own grant and receipts.
- No provider authentication, resource provisioning or public release is established.

Future implementations must generate named failures from actual evaluation output and preserve incomplete evidence honestly.

See [INTERFACE.md](INTERFACE.md) and the [ledger contract](../src/llm_router_eval/shared/callledger/README.md). Wire declarations alone do not reserve money; the ledger performs atomic accounting with trusted registered evidence. No live price/token bounds, actual image validation or arbitrary generation-output schema execution are established. A charge above its bound is recorded and halts further funding; an incorrect bound can cause actual spend above the cap.
