# Optional native SDK producer

The default CLI remains an authored offline comparison. The reusable provider package
also exports `SDKTransport` and `ProjectCredential`. Install its pinned optional dependencies:

```sh
uv sync --frozen --extra sdk
uv run --frozen --extra sdk pytest tests/providers/test_sdk_wire_worker.py tests/providers/test_sdk_transport.py tests/providers/test_missing_usage_settlement.py
```

The separate Windows CI job explicitly installs this extra and exercises these
mock controls. Its dependency setup needs public package downloads; the tests
do not require provider credentials or real provider requests. The default
Ubuntu job continues to check the offline core without the optional SDK extra.

The implemented native scope is text-only OpenAI Responses `gpt-6-luna` and TypeSafe
`jev-1.13.0` `system_one`, at most 32,000 payload bytes, 8,000 UTF-8 string-value bytes,
512 OpenAI output tokens, and 65,536 response bytes. Streaming, tools, images,
nondefault endpoints, provider retries and live content repairs are excluded.
Native OpenAI preparation explicitly binds `service_tier="default"` before admission,
so an account's automatic service-tier preference cannot change the requested tier.
The native response limit must be between 1 and 65,536 bytes. Unsupported limits
return `unsupported_sdk_response_limit` during preparation or operation admission,
before funding is reserved. A caller using direct Ledger admission receives the same
refusal before any dispatch marker or unknown liability is created; its existing
reservation is preserved for the caller to disposition.

## Consumer integration

Keep every existing `Provider.run`/`complete` accounting keyword. Construct an
`SDKTransport(flavor, credential=ProjectCredential(account_handle, explicit_key))`
only after the caller has verified account ownership and its concrete authorization.
The credential object suppresses its key in representations. This library never reads
environment credentials, secret files or account defaults. An account-handle string
alone does not prove account ownership, spending authority or usable credentials.

1. Use `Provider.prepare` with the **same SDK transport** to obtain the native prepared
   bytes before registering request evidence. OpenAI SDK field order differs from
   the offline adapter. The semantic request/inference identities remain separate
   from the actual dispatched-byte digest.
2. Register that exact request with independently established canonical pricing and
   capacity evidence. `mode="live"` maps to the real Ledger's `external` mode, which
   refuses synthetic funding and price provenance. Admit one-attempt, zero-repair
   children with caller-funded conservative bounds before `run`.
3. `run` rechecks allocations and transfers the durable reservation before sending.
   A child process receives the explicit credential over an anonymous pipe, with
   provider/proxy environment defaults removed. The pinned SDK's exact bytes,
   POST method and direct official URL are checked before physical transport entry.
   A changed serializer refuses rather than sending different funded bytes.
4. The worker permits one send, disables SDK/HTTP retries and redirects, requests
   identity encoding, rejects encoded responses, and caps response bytes before
   either SDK decoder. Only bounded raw body and safe retry headers return to the
   controller; raw SDK exception text and credential headers never cross the pipe.
5. The parent owns the wall deadline, including startup and pipe writes. Windows
   uses a kill-on-close Job with an eight-process ceiling; POSIX uses an owned
   process group and observes its leader without reaping before group termination.
   The child is closed before returning. First-output timing remains unavailable.
   A timeout/lost response retains conservative unknown liability in the Ledger;
   killing a local process does not prove that a remote request was unbilled.

A valid answer does not establish a known settlement. If normalized usage cannot
support the bound pricing policy, the controller retains unknown liability and
stops further retries or repairs for that child. A previously usable answer returns
`status="unknown"` without usable output, retaining its actual accounting reference,
attempt IDs and source provenance. Refusal/error outcomes remain unavailable with
the same honest dispatch accounting. The failed child makes the operation partial;
it does not turn the dispatch into `not_run` or a zero-cost source observation.

The worker module is an internal implementation detail. Callers must use the accounting
controller and public transport, and must not invoke the worker or SDK directly to
bypass admission. The CLI has no live dispatch command or automatic key loader.

## Mock evidence and remaining gates

`SDKTransport(flavor, mock=...)` accepts authored base64 response controls and always
reports `is_test_transport=True`. It can only join `injected_simulation`; a live
Provider rejects it. Its child positively verifies a socket-denial guard before
SDK imports. Real SDK serialization, decoding and failure paths run against mocked
HTTP, so the returned provider result remains `origin=mock`, source origin synthetic,
and current external charge zero. These observations establish no real usage,
account access, price bound, model quality or external success.

Local Windows controls exercise an observed mock send blocked longer than its hard
deadline, actual Job closure, one durable attempt and retained unknown liability.
The POSIX implementation has not been runtime-qualified in this delivery. The earlier
SDK timeout/environment comparison failures remain preserved; fresh successful
checks do not relabel those failures. Optional dependency lock URLs remain public
and portable, and previous base dependency versions are retained.

The exact canonical producer and its later unknown-usage repair have independent
bounded acceptance for SDK mocked-wire accounting and Windows worker closure.
The repair review executed the 12 affected cases and two separate wire witnesses,
including genuine Ledger completion proof, one attempt and retained unknown
liability. These observations establish the producer contract; each consumer must
still adopt the exact source and verify its own actual accounting join. The
producer-side unavailable-result projection is a declared wire stub.

Before any paid use, the exact producer publication and lock require independent
review; each consumer must adopt that source and pass its own accounting join.
Trusted project-owned credential injection and canonical conservative price/liability
bounds remain necessary. No paid request, real account access, heldout evaluation or
finished-app acceptance is established by this implementation.
