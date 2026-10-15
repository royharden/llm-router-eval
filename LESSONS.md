# Implementation lessons

- Bind private labels, fixed item populations and method configurations separately from public routing requests. A private-label edit must not change a capture/request key; a nested edit after freezing must refuse before execution.
- Keep missing/stale/failed cases in their declared population, and keep unvalidated rubric items unscored. Synthetic controls and structural trust doubles do not establish independent references or model quality.
- For exact HTTP payloads, validate framing and stop at the verified Content-Length/pinned size. The standard-library HTTPResponse can close its file pointer on the final successful read; dereferencing it to seek another EOF read can fail after a complete transfer. Cached standard-library controls should cover exact length, truncation, oversize data and deadlines before network execution.
- Keep monotonic start time separate from a deadline. Shortening a deadline must not inflate an elapsed-duration calculation that assumes the original budget.
- Persist the actual before/after inventory difference, not only an unchanged boolean. A later matching snapshot does not explain or erase a failed transient comparison.
- A bounded source-probe timeout establishes an incomplete probe. Installed package bytes and static source/signature hints do not prove imports, retries, cancellation, account access, model support or billing. CPython audit hooks are instrumentation, not a native operating-system sandbox.
- Windows virtual-environment launchers may create another Python process. Use actual owned process handles and descendant lifecycle accounting; a launcher exit alone does not establish complete closure.
- A malformed successful fixture may be refused before policy execution. Model the explicitly failed capture honestly when demonstrating downstream refusal/escalation, and report partial collection instead of inventing a complete matrix.
- Verification wrappers must bind the correct candidate and output destination before writing receipts. Retain incorrectly attributed or timed-out attempts and exclude them from acceptance; do not relabel them as passing evidence.
- Prefer a complete small local workflow and honest portability handoff before adding cloud/UI dependencies. Source tests do not replace independent app review or actual corpus/model evaluation.

- Report source-cost completeness separately from current charge: failure before retaining a full matrix must not turn unavailable source cost into a zero total. Zero passes leaves cost-per-pass undefined, and long valid decimal values must retain their cents in aggregate reports.
- Exercise the actual local decoder when demonstrating malformed structured wire. A manually authored failure status can test downstream fallback but does not prove the decoding boundary ran.
