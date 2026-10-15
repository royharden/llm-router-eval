# Anchored scoring and calibration boundaries

`shared.routing.scoring` owns private `ScoringLabel` objects and deterministic `score(label, candidate)` output. It is not imported by policies or their public context. `CandidateAnswer` binds the prompt ID, provider status, transient response and optional capture hash. Provider errors, budget rejection, unknown outcomes and missing responses are assigned failures. Score records contain IDs, kind, closed reason/status and pass/fail/unscored; they omit private answer/rubric and response bodies.

The universal single `FINAL: <value>` line gate is shared with cascade formatting. Numeric scoring then requires one plain signed Decimal; no commas, units, scientific notation, multiple numbers or nonfinite values. Code scoring requires a plain integer. MC scoring requires one uppercase A–D token. Exact text is literal, including case and punctuation. Correct values in working cannot override a wrong final line. These deliberately strict grammars must match future prompt/source preparation. No prompt is executed.

Rubric responses currently remain `unscored/judge_unvalidated`. There is no actual trusted judge, human calibration or validated-rubric integration. `summarize_scores` exposes assigned, recorded, scored, passed, failed, unscored and missing counts; missing or unvalidated results never disappear from assigned denominators. The later report must keep the deterministic heldout headline separate from unvalidated rubric items.

## Blinded label inputs

`scripts/label_judge.py` writes a new blank label template from caller-selected pair metadata. It fills no answers, human identity or judge outcomes and refuses overwrite. Pair IDs should already be opaque in the actual human workflow. Bodies needed for human judgment and actual blinding/owner review remain separate required inputs; a template is not proof that a person inspected anything.

`HumanAdmission` is an explicit trusted caller input binding exact human file, identity, excluded agents, blind manifest, full pair manifest and owner receipt. Data cannot supply its own authority. The full manifest includes candidate source/cohort; changing corruption to natural therefore invalidates admission even though hidden cohort fields are omitted from the blind projection. Unit tests use plainly identified synthetic admission doubles, not real human labels. Actual source rights, independent/human identity and blinding require their real owner records.

## Domain calibration wrappers

Checker wrappers expose the predeclared0.5/0.7/0.9 grid, accepted counts, false accepts/allaccepted, false accepts/humanfails, coverage, missing predictions and source cohorts. A threshold requires>=40 dev responses, >=20human-fail examples, >=20accepts and false accepts/allaccepted<=5%, plus complete verified observations. Synthetic diagnostic candidates create no operating permit. A policy permit additionally needs a separately trusted exact report receipt; the policy rechecks its actual model/config/instruction scope. Heldout observations cannot tune a threshold.

Judge wrappers require60 declared pairs from20 prompts: cheap/frontier natural40 and separately corrupted20. Natural agreement, false-pass/human-fail, class counts and missing observations remain separate; corrupted examples cannot improve natural-only acceptance. The fixed inclusive gate is agreement>=.90, conservative lower>=.80, falsepass/humanfails<=.05 and>=20natural human-fail examples.

The approved conservative lower is the minimum of a two-sided95 Wilson lower and a whole-prompt-cluster2.5-percentile lower, with2000replicates/seed20261014. Both bounds, kappa, class counts and limitations must be reported. This package contains **no generic Wilson/bootstrap/kappa implementation**. Its typed request/evidence port describes S3 domain inputs without guessing an unpublished S2 API. Actual computation must use the canonical S2 source after verified source adoption. Current synthetic hand-count bounds are diagnostics; absent/untrusted canonical source yields null statistics or unvalidated status and never authorizes a rubric headline. Finite cluster intervals on few groups do not guarantee nominal coverage.

No calibration, human agreement, model quality, real provider result or finished-app acceptance has occurred. The remaining canonical-statistics/rubric bridge, licensed corpus, real labels and actual execution remain required gates.
