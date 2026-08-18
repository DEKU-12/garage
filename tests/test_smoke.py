"""Day-0 smoke test so `main` is green from the first commit (rules.md §5).

Replaced by real coverage as modules land: patch parsing, event round-trip,
reducer fold determinism, reporter math against golden CSVs (rules.md §4.3).
"""

import importlib


def test_engine_packages_import() -> None:
    for name in (
        "engine",
        "engine.agents",
        "engine.repo",
        "engine.eval",
        "engine.accounting",
        "engine.report",
    ):
        assert importlib.import_module(name) is not None
