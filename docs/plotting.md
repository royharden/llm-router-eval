# Authored figures and reproducibility

Add `--plots` to a normal evaluation or demonstration using a fresh output path:

```sh
uv run --frozen python -m llm_router_eval eval --tasks fixtures/implementation-controls/tasks.json --vectors fixtures/implementation-controls/requests.json --out ../router-figures-001 --plots
```

The command produces `quality-cost.png` (3120×1680), `quality-cost.svg` and `figure-data.json`, alongside the reports and SQLite accounting. Report JSON contains the three artifact hashes. The table retains exact total-cost strings and assigned counts as well as floating-point coordinates used by the renderer. Re-rendering the same retained report reproduces the same bytes with the pinned runtime/fonts; a new evaluation has new local timing observations.

The quality/cost panel uses the deterministic eligible population for both axes. It includes every executed policy and the fixed-grid random/oracle references with complete cost/quality points. Unknown source costs and disabled methods are omitted with explicit reasons and lower/unknown-upper bounds in the table. No finite source upper bound is invented from the current replay charge. A retained code-only execution can consume zero model sources; host cost remains unknown. Overlapping points remain at their actual coordinates.

The timing panel shows local policy computation on the same eligible cases: median bars, p95 lines and actual `n` values. Fixture lookup, policy computation and real model inference are different measurements. Model latency, TTFT and report-only reference execution latency remain unmeasured. The figure's synthetic watermark and these labels must be retained when sharing an authored-control illustration.

## Family sampling

The unchanged canonical S2 paired whole-family engine supplies 95% percentile intervals using one shared draw stream, seed `20261014`, and Python 3.12. Its v1 profile requires **2,000** draws; this replaces the approved plan's proposed 1,000 without a separate statistics implementation. The report records the draw digest, support, version and per-method undefined reasons. At least two whole families are required. Missing outcomes remain in the assigned denominator; rubric cases remain outside deterministic quality. All grid references are shown rather than selected as tuned winners.

These intervals describe sampling of the authored controls. They do not establish model quality, independently sampled benchmark performance, trusted rubric calibration or heldout acceptance. Real corpus/label/controller inputs and actual provider observations remain separate prerequisites.

## Re-render a retained report

Run from a fresh Python 3.12 process. The headless renderer owns a temporary font/config cache and uses the pinned Matplotlib Agg backend:

```python
import json
from pathlib import Path
from llm_router_eval.evaluation.plotting import render_figures

report = json.loads(Path("../router-figures-001/report.json").read_bytes())
target = Path("../router-rerender-001")
target.mkdir(exist_ok=False)
for name, content in render_figures(report).items():
    (target / name).write_bytes(content)
```

## Dependency notices

Matplotlib 3.11.2 and its compatible dependencies are pinned by the portable uv lock. The installed wheels retain their complete original licence/copyright/notice files; this repository does not vendor their binaries. Matplotlib's PSF-based licence includes additional bundled-component notices. FreeType is used under its FreeType License option:

Portions of this software are copyright The FreeType Project (https://www.freetype.org). All rights reserved.

NumPy's Windows wheel includes OpenBLAS/LAPACK and GCC runtime notices, including `GPL-3.0-or-later WITH GCC-exception-3.1`. Pillow includes its codec notices and FontTools includes external font notices. Preserve those upstream texts when redistributing dependency wheels. The installed notices are authoritative for the exact binary edition; a package's headline SPDX field is not the whole bundled-component inventory.
