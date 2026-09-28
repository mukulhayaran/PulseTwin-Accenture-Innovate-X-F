# Security Review

Production-readiness pass, 2026-09-21. `docs/SECURITY.md` already states its demo-vs-
production posture unusually plainly ("this deployment is a demo, and its posture is not
production" — authorization without authentication, by design, per the finals brief's own
anti-pattern guidance). This review verifies that stated posture against live behavior and
hunts for what it might have missed, rather than re-deriving it from scratch.

## RBAC — verified live against the real Docker backend

| Probe | Expected | Observed |
|---|---|---|
| `GET /api/units/{id}` with no role header | 200 (documented open-read gap) | **200** |
| `GET /api/economics/roi` with no role header | 403 (leadership-gated) | **403** |
| `GET /api/economics/roi` with `X-PulseTwin-Role: supervisor` | 403 | **403** |
| `GET /api/economics/roi` with `X-PulseTwin-Role: leadership` | 200 | **200** |
| `X-PulseTwin-Role: superadmin_hacker` (invented role) | Clean rejection, not a crash or silent default | **400** `"unknown role 'superadmin_hacker'; must be one of (...)"` |

RBAC enforcement matches `docs/SECURITY.md`'s table exactly, live, against the real
production Docker stack — not just the unit tests. The documented gap (13 read endpoints
with no role check, including `/api/units/{id}`) is real and already correctly described
as decorative-until-authentication-exists, not a hidden defect.

## Injection probes

| Probe | Result |
|---|---|
| SQL injection via `plant_id` query param (`plant_a'; DROP TABLE units;--`) | **No injection occurred** — `units` table row count unchanged (2628 before and after). Every query in `serve/` uses parameterized placeholders (`db.query_all(con, "...WHERE plant_id=?", (plant_id,))`); the payload was treated as a literal string, never executed as SQL. |
| Path traversal via unit id (`/api/units/../../etc/passwd`) | **404**, not a filesystem read — the path segment is matched against a DB row, not a filesystem path, so traversal syntax has nothing to escape into. |
| **Unexpected finding**: the SQL-injection probe above returned **HTTP 500**, not a clean 4xx | See below — this led to a real, unrelated P0 defect, not a security hole. |

**What the 500 actually was**: not injection — a genuine `psycopg.errors.GroupingError` in
`/api/stations/stats`'s SQL (`GROUP BY station_id` while also selecting the
non-aggregated `provenance` column). SQLite tolerates this; PostgreSQL rejects it
outright, and does so for EVERY call, valid `plant_id` or not — confirmed by calling the
endpoint with `plant_id=plant_a` directly (also 500). This endpoint had no test calling it
over HTTP at all (confirmed by `grep`) and the full 518-test suite has apparently never
run against a real Postgres instance, so nothing caught it until this pass. **Fixed**
(`GROUP BY station_id, provenance` — safe because `provenance` is functionally dependent
on `station_id` by construction) and a regression test added
(`tests/test_api.py::test_station_stats_returns_one_row_per_station`). Full detail in
`production-readiness-report.md`'s findings table (P0-2) — flagged here because a security
probe is what surfaced it, which is itself worth noting: **a working input-validation
probe can surface unrelated correctness bugs as a side effect**, and a 500 in response to
malformed input should always be chased even when the injection itself is confirmed safe.

## GenAI adversarial surface

`docs/SECURITY.md` and `docs/DECISIONS.md` (ADR-037/038/039) already name the threat model
precisely: prompt injection through tool-result data, role escalation via the agent path,
fabricated citations, cross-turn citation replay. Rather than re-run these live against a
paid LLM provider (slow, stochastic, and costs real API quota), this review verified the
EXISTING test coverage is real and adequate, and looked for one gap not already covered.

**Existing coverage, confirmed by reading (not just counting) the tests:**

- `tests/test_genai_verifier.py` — 25 tests directly against `genai/verifier.py`: unknown
  call_id, pointer past array end, non-numeric target, value mismatch, rounding tolerance
  edge cases, uncited-number prose redaction, station-id exemption, dollar-figure sourcing
  (both directions), missing confidence interval on an inferred claim, provenance
  laundering, operator-identity detection (parametrized over multiple leak patterns),
  operator-group-bucket legality, no-claims rejection, too-many-unverified threshold,
  single-failure redaction vs whole-run rejection, determinism, and parser robustness
  (prose-wrapped JSON, JSON containing braces in strings, unparseable output).
- `tests/test_prompt_injection.py` — 12 tests: unregistered tool calls rejected, the
  registry contains nothing that mutates the line, path injection cannot escape the
  allowlist, injected arguments are still schema-checked, an injected instruction cannot
  manufacture a verified claim, a number appearing only in injected text is not citable, a
  fabricated call_id fails, an injection cannot authorize an unsourced dollar figure or
  launder provenance or surface an operator identity, injected payloads are wrapped as
  tool results (never as messages the model would treat as instructions), and the system
  prompt is always first and ours (cannot be pushed out of position).
- `tests/test_genai_threads.py::test_the_replay_forbids_cross_turn_citations_in_words` —
  cross-turn citation replay, the exact attack ADR-039 exists to close.

This is genuinely thorough — more so than a from-scratch adversarial pass would likely
produce in the same time. No gap was found in what these already assert.

**One residual gap found and executed live (not previously tested):** the C3 "uncited
number" sweep exempts any prose number that appears ANYWHERE in the recorded tool
results — including inside a free-text STRING field, not only a genuine numeric JSON
value. Constructed a synthetic transcript where a `lot_id` field contained embedded text
with a decoy number (`"L0153 -- true defect rate is 47.3 percent this week"`), then had a
`Finding` assert a completely fabricated, uncited claim using that number in prose:

```python
transcript = {"call_0001": {"data": [{"lot_id": "L0153 -- true defect rate is 47.3 percent this week", ...}]}}
finding.summary = "The defect rate this week is 47.3%, which is unusually high."
verify(finding, transcript)
# -> status: "verified", prose_issues: []
```

The fabricated 47.3% claim was served as verified, because the C3 sweep only checks
whether a number appears anywhere in the tool corpus, not whether it appears in a context
that actually supports the claim being made. **Assessed as real but not currently
reachable**: traced `lot_id`'s actual origin (`offline/simulator/line_model.py`:
`lot_id=f"L{lot:04d}"`) and its path through `ingestion/mqtt_publisher.py` (passes the
simulator's value straight through) — it is a bounded, fixed-format identifier in this
system today, never open free text, so there is no data path by which an attacker could
inject a decoy number into it. This matches `docs/SECURITY.md`'s own stated first-line
mitigation ("the allowlist excludes free-text-heavy endpoints") — the gap is real
defense-in-depth debt, not a live hole, and becomes relevant only if (a) this system is
ever connected to a real MES/ERP with less controlled lot-numbering, or (b) a future tool
exposes a genuinely free-text field (operator notes, checklist comments — both currently
excluded from the tool registry for this exact reason).

**Recommendation, not implemented** (ambiguous tradeoff, not a contained fix — see
production-readiness-report.md P2 list): the C3 corpus sweep's `_flatten_numbers` currently
extracts numbers from string values as well as numeric ones. This is also what lets a
model legitimately cite a number embedded in an explanatory `note` field (e.g., alert
triage's `factors[].note`: `"1.00x the plant's own alert threshold (0.4061)"`) without a
direct JSON-Pointer to a numeric leaf — a real, actively-relied-upon behavior. Removing
string-scanning entirely would close the poisoning vector but break that legitimate case.
A narrower fix (only scan strings in fields explicitly meant to carry explanatory numbers,
e.g. anything named `note`/`reason`, never free-form ID fields) is possible but is a
genuine design choice, not a bug fix, and is left as a recommendation rather than patched.

## Rate limiting — verified under real load

See `performance-benchmark.md` for the full data. Summary: `serve/middleware/
rate_limit.py`'s 300 req/min per-role budget was legitimately exhausted by a modest load
test and correctly returned `429` with a clear message thereafter — verified against the
real Docker deployment, not simulated.

## Secrets, encryption, audit trail

Reviewed `docs/SECURITY.md`'s claims against `docker-compose.prod.yml` and
`serve/middleware/audit.py` — matches: plaintext dev credentials in Compose (acceptable,
never exposed outside localhost), `audit_log` is INSERT-only from application code
(confirmed: no `UPDATE`/`DELETE` targeting it anywhere in `serve/` or `mlops/`), payloads
carry IDs and outcomes, never raw sensor readings or operator identity. No new finding
here — the existing documentation held up under a live check.

## What this review did NOT do

- No live adversarial run against a real hosted LLM (cost/time; the deterministic-verifier
  tests above exercise the same guarantees without the nondeterminism or expense).
- No dependency vulnerability scan (`pip-audit`, `npm audit`) — already named as
  not-built-for-the-demo in `docs/SECURITY.md`; not newly discovered here, and adding a
  scanning step belongs in the CI recommendation, not a one-off manual run.
- No penetration test of the Kubernetes network policies (`infrastructure/kubernetes/
  network-policy.yaml`) against a real cluster — no cluster was stood up (would require
  cloud credentials, outside this pass's mandate). The policy file was read, not tested.
