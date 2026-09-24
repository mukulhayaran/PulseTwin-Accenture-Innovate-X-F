"""Offline experimental benchmarks.

Nothing in here is imported by `serve/` (the import wall, CLAUDE.md section 2) or by the
normal build pipeline. These modules run experiments, write machine-readable artifacts
under `artifacts/benchmarks/`, and are invoked by hand or by the nightly CI workflow.
"""
