"""PulseTwin GenAI tier.

A third offline deployable alongside `ingestion/` and `mlops/`. NOTHING under `serve/`
imports this package -- `genai` is absent from the ALLOWED set in
`tests/test_serve_imports.py`, so the existing import-wall test already fails on any
`import genai` inside `serve/`. That test needed no edit to cover this package, which is
the point: the wall held without being widened.

See docs/DECISIONS.md ADR-037 for why the tier sits outside the wall and outside the
backend's namespace, and why its internet egress does not contradict
`configs/deployment.yaml`'s `internet_egress_allowed: false`.
"""
