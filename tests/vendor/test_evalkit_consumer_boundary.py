"""Exact owner runtime bytes are the consumer boundary, not an app acceptance claim."""

import ast
import hashlib
from pathlib import Path

from llm_router_eval.shared.evalkit import statistics

EXPECTED = {
    "README.md": "1308c679caa5413229ba41de0e6fc4f9cdfd8d76fcb66c0cb1aa4597d2dade94",
    "__init__.py": "3017cebcf1f80d410a61712cd8207c1803e09954936ce33238d886760683843e",
    "corpus.py": "427ade409f4fb83b59a2d9952c890cb830096af948e1dc9d393a8aef47c23fa0",
    "identity.py": "5897c07d4b0f5d55dd8fee8cb74a325caaebc03b9e70bbf9c426c431d968f1b2",
    "interfaces.py": "d23bc8be5188a968388272580b259ad4af4b1a4cbf393be52d7933514235ed9d",
    "models.py": "b18613383987a24569e7a9a133b2c212d858c2cf9ee147720b350c06126596c4",
    "reporting.py": "96d439870fe13d5c2ff5064d34eeb66fd5a96cf86fa5e5983ba243a6279aa283",
    "resampling.py": "005c21b6d9f113db22132f0a660c3a2713e5aeaffb4dbb3c4c5349d66c194619",
    "runner.py": "b3a9078cf3265d750296907572dfeb66de780c01028b715cfdafb39dbad7bccb",
    "schema.json": "b44b9f4cea1b90d6524ddf9aa56327667a92340810b19000bb8d47702695ffb7",
    "statistics.py": "9e9ec5e9ba86489c8938a87bd064debbedc143126952959729ab46fd624784dc",
    "LICENSE": "431665582156c442d0448d1f5cd2e45663ab96e6cf098711ffa58c4bb01b69e6",
}


def test_actual_canonical_runtime_bytes_and_import_closure():
    root = Path(statistics.__file__).parent
    for name, digest in EXPECTED.items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest
    for source in root.glob("*.py"):
        for node in ast.walk(ast.parse(source.read_text())):
            if isinstance(node, ast.ImportFrom) and node.level:
                continue
            if isinstance(node, ast.Import):
                names = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            assert all(
                n.split(".")[0] not in {"rfr", "llm_router_eval", "openai", "typesafe"}
                for n in names
            )
