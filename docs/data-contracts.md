# Local data admission and seals

The data module validates local inputs and generates family splits. It does not download a corpus or execute evaluation. No licensed 300-task corpus, independent heldout Gold or blinded human calibration labels are included.

`Task` contains public prompt data and a separate `ScoringData` object. `routing_view()` explicitly returns only task ID and prompt. Source, family, kind, difficulty, answer and rubric are absent from that view. Changing private scoring data therefore cannot alter routing input. Every task binds its full row to a SHA-256 digest; duplicate task IDs, prompts, source rows, missing sources, unknown fields and oversized prompts reject.

`SourceProvenance` records a source revision, artifact hash, source-row identity, licence declaration, rights receipt and author role. Synthetic implementation controls have no rights receipt and cannot qualify as a corpus. `load_dataset` requires a separately supplied `DatasetAdmission` for any `frozen_corpus`: exact file hash, exact source grants, actual independent-author membership, excluded tuner identities and freeze receipt. Data cannot supply its own admission. These are trusted caller inputs, not cryptographic verification of human identity or source rights. The caller must obtain the actual approved evidence first. Source extraction from licensed upstream artifacts remains pending that input; a declared hash alone does not demonstrate faithful extraction.

`split_families` uses deterministic hash ordering and exact subset sum within each task kind. It never divides a family to reach a target. Odd counts, mixed-kind families and infeasible partitions reject. `validate_benchmark` requires separate admission, the prescribed 100 GSM8K / 100 MMLU / 40 code / 20 exact / 40 rubric mix, seed 20261014 and a 150/150 family-separated split. Unit tests exercise this shape using an explicitly synthetic trust double; they establish no actual corpus rights, independent labels or benchmark result.

`ConfigurationSeal` binds dataset, split, models, prices, policies, thresholds, judge, sources and input manifest. `CampaignJournal.start` binds actual split IDs and the seal; appending refuses configuration changes, unknown cells and completed-cell replacement. These are immutable local validation records. Durable once-only campaign ownership, crash recovery and heldout execution belong to the later runner. Serializing or resetting a journal is not independent experiment authority.

## Tiny implementation workflow

Run with the installed package from the repository root. Each command creates a new output and refuses an existing one:

```sh
python scripts/make_synthetic.py --output /chosen/new/tasks.json
python scripts/prepare_data.py --input /chosen/new/tasks.json --output /chosen/new/split.json
python scripts/make_fixtures.py --output /chosen/new/requests.json
```

The committed ten controls under `fixtures/implementation-controls` regenerate byte for byte. Their request vectors use the real generation wire model but are not `Provider` captures, independent Gold, model answers or live observations. The default workflow never requires API keys or network access. Corpus preparation with `--benchmark --admission <actual trusted admission file>` remains unavailable until those real input grants exist.
