# Security

Answers to the finals judges' question "show me the security architecture" — what is
actually built, what is documented-but-not-built for the demo, and the upgrade path
between the two. Every claim here names the file that backs it; nothing below is aspirational
without saying so.

## Authentication

**Built, two layers.**

*The header.* `X-PulseTwin-Role: supervisor | manager | leadership | system` and
`X-PulseTwin-User`, read by `serve/middleware/auth.py`. The latter attributes manual
decisions and defaults to `demo-<role>` when omitted. A caller that presents nothing
else is trusted on its own word, exactly as before — deliberately, per the finals brief's
anti-pattern guidance ("do not implement full OAuth/SSO — a role header is sufficient for
the demo").

*The session (ADR-048).* `POST /api/auth/login` checks one credential per role
(`serve/routers/auth_demo.py`, constant-time compare) and mints an opaque 256-bit token
held in `serve/session_store.py` with an 8-hour TTL. `GET /api/auth/session` validates
one; `POST /api/auth/logout` revokes it. When a request carries `X-PulseTwin-Token`, the
**session decides the role and the declared header cannot raise it** — a declared role at
or below the session's is honoured (the deliberate privilege drop `/ops`'s "Viewing as"
selector performs), anything above it is ignored, and an unknown or expired token is a
`401` rather than a quiet fall-back to the header. `get_user` takes the session's user, so
a token-holder cannot relabel who acted in the audit trail.

*Where the passwords live.* Each comes from `PULSETWIN_PASSWORD_<ROLE>`. The repo's
committed passwords are a local-dev fallback only: when `PULSETWIN_REQUIRE_POSTGRES=1`
(every deployment, ADR-052) an account with no env password cannot sign in at all, so the
public repo never opens a deployed instance. Pinned by
`tests/test_auth_session.py::test_deployed_instance_refuses_the_public_repo_passwords`.

**What this does and does not close.** The UI's own path through the app is now a role the
*server* issued and can revoke, which is what makes "a manager cannot open the leadership
view" enforceable rather than a claim the client makes about itself. It does **not** close
the header seam: a caller that presents no token and sets `X-PulseTwin-Role` by hand
behaves exactly as it did before sessions existed, and `tests/test_auth_session.py` pins
that on purpose. The store is also process-local, which is right for the demo's
`--workers 1` deployment and wrong for several replicas — the fix there is the shared
store or the stateless JWT below, not a larger dict. Full authentication is still the
upgrade path, not the current state.

*Session mode closes the header seam (ADR-063).* `PULSETWIN_AUTH_MODE=session` is set in
`docker-compose.prod.yml` and `render.yaml`. In it, a bare `X-PulseTwin-Role` above
`supervisor` is a `401` ("requires signing in"). Only a server-issued session grants a
role, and a header can only lower it. No credential at all is the public floor view
(supervisor). A spawned GenAI run gets its own session, whose TTL is the worker stale
limit, through the `PULSETWIN_API_TOKEN` environment variable, never argv. `dev` mode
keeps the old trust-the-header behaviour for local work and logs a warning at startup.
Pinned by `tests/test_auth_session_mode.py`.

**Production upgrade path:** replace the header with a JWT issued by the plant's OIDC
provider (Azure AD, Okta), validated with `python-jose` (already on the import
allowlist, ADR-033, specifically for this). The swap is small and isolated:

```python
# serve/middleware/auth.py, production version
def get_role(request: Request) -> str:
    token = request.headers.get("Authorization", "").removeprefix("Bearer ")
    claims = jwt.decode(token, OIDC_JWKS, algorithms=["RS256"], audience=API_AUDIENCE)
    role = claims.get("pulsetwin_role")          # mapped from an AD group at token-issue time
    if role not in ROLES:
        raise HTTPException(403, "no valid role claim")
    request.state.role = role
    request.state.user = claims.get("sub")
    return role
```

Every router already calls `Depends(require_role(...))` (`serve/middleware/auth.py`), so
no router code changes — only this one function's body does. `require_role`'s hierarchy
(`supervisor < manager < leadership < system`) does not change either.

## Authorization (RBAC)

Strict hierarchy — each role can do everything the one before it can (`serve/middleware/auth.py`).

**The hierarchy governs the API. It does not govern which view you may open.** Those are
two questions and ADR-048 separates them: `leadership` may *call* `POST /api/whatif`, and
`leadership` may not *enter* `/manager`. Persona views are separations of duty, which a
rank comparison cannot express — under the previous entry check a leadership login walked
into `/manager` and `system` walked into all four. `frontend/lib/routeAccess.ts` is the
single exact-match table both the route guard and the navigation rail read.

| View | May enter |
|---|---|
| `/overview`, `/supervisor` | anyone, signed in or not — the floor view is where an unauthenticated visitor lands, and every endpoint it calls is already at the API's own default role |
| `/manager` | `manager` |
| `/leadership` | `leadership` |
| `/ops` | `system` |
| `/copilot` | `manager`, `leadership`, `system` — the three its own endpoints accept |

A signed-out visitor acts as `supervisor` (matching this middleware's own default: least
access, never most) and is redirected to `/login?next=<path>` off any gated view. A
signed-in role on the wrong view gets `AccessRestricted`, not a redirect.

**This table is generated from the actual `Depends(require_role(...))` declarations in
`serve/`, not from intent.** Verify it with
`grep -rn "require_role\|Depends(get_role)" serve/`:

| Minimum role | Endpoints |
|---|---|
| **none — open** | `/api/health`, `/api/config/plants`, `/api/config/stations`, `/api/stream`, `/api/predict/defect`, `/api/units`, `/api/units/{id}`, `/api/trace/containment/{id}`, `/api/bottleneck/current`, `/api/bottleneck/history`, `/api/stations/stats`, `/api/metrics`, `/api/whatif/stations`, `/api/triage/availability` |
| `supervisor` (any valid role) | `/api/alerts/escalation-policy`, `/api/system/sensor-dropouts`, `/api/alerts/triage`, `/api/system/data-quality`, `/api/trace/evidence-graph/{id}`; and, via `get_role` rather than `require_role`, `POST /api/feedback` and `POST /api/alerts/{id}/acknowledge` — these accept any role but *record which one acted*. **Signed-in only (ADR-066):** `POST /api/triage/runs` and `GET /api/triage/runs/{id}` for the `incident` and `handover` intents alone — no free-text question, no thread, no raw tool evidence |
| `manager` | `POST /api/whatif`, `/api/system/health`, `/api/system/ingestion-lag`, `/api/shift/summary`, `POST /api/triage/runs`, `GET /api/triage/runs`, `GET /api/triage/runs/{id}`, `GET /api/triage/threads`, `GET /api/triage/threads/{id}`, `POST /api/recommendations/{id}/approve` / `reject` (ADR-069; the ADR-051 proposal endpoints were retired, ADR-073) |
| `leadership` | `/api/economics/roi`, `/api/economics/instrumentation-plan`, `/api/model/health`, `/api/model/registry`, `/api/audit/log`, `/api/audit/actions` |

`system` outranks every row above but gates no endpoint of its own: the retrain, promote
and rollback admin endpoints it used to guard were removed with retraining (ADR-053).

`/api/system/sensor-dropouts` is the narrow, supervisor-level slice of `/api/system/health`:
the same active-dropout list, without the uptime and connection-pool internals. It exists
because `/supervisor` pins itself to the `supervisor` role and must render
`SensorDropoutAlert` — a floor supervisor needs to know their own sensor is dark. Before it
existed that page polled `/api/system/health` and took a silent `403` every ten seconds, so
the dropout banner never appeared. Splitting the endpoint was chosen over widening the
`manager` gate precisely so this table keeps meaning what it says.

**Known gap, stated rather than papered over.** The read endpoints in the first row carry
**no role check at all** — including `/api/units/{id}`, which returns a unit's defect risk,
contributing factors and full station journey. The gating that exists is concentrated on
*mutations*, *economics* and *operations*, which is where the audit and least-privilege
arguments actually bite, but "supervisor-level reads are open" is the honest description
of today's behaviour, not "supervisor role required." Closing it is a one-line dependency
per route; it has not been done because with no authentication in front of the header
(see above) a read gate would be decorative. **When real authentication lands, gate these
reads in the same change** — otherwise the RBAC story is stronger on paper than in the
routing table. ADR-048's session token narrows this: a request that carries one is
authenticated, so gating these reads would now mean something for the UI's path. It is
still not done, because the un-tokened header path remains open by design and a gate that
any caller can step around by dropping a header is the decoration this paragraph is about.

Enforcement of what *is* gated is server-side only and cannot be bypassed from the client
— `require_role` raises `403` regardless of what the UI renders, and
`tests/test_auth.py` asserts the 403s per role. `frontend/components/shared/RoleGate.tsx`
is a client-side *mirror* of this table, not a second enforcement point: its own comment
says so ("this does NOT replace server enforcement"). `frontend/app/ops/page.tsx` is the
one view built to demonstrate the hierarchy live — a "Viewing as" selector lets a demo
drop from `system` to `manager` and watch the model-health, drift and audit-log panels
gate themselves off in real time via `RoleGate`, rather than only
being described in this document.

**Rate limiting** (`serve/middleware/rate_limit.py`) is per-role, in-memory, single-process
— correct for the demo's `--workers 1` deployment (ADR-009). A multi-replica production
deployment would move the counters to Redis (already in the stack for the ingestion
feature buffer) so limits are shared across pods; documented here rather than built,
since the demo never runs more than one backend replica.

## Audit trail

Every mutating action is logged to `audit_log` (`migrations/002_audit_log.sql`,
`serve/middleware/audit.py::log_action`): timestamp, role, action, resource type/id, a
JSON payload, IP address. The eight covered actions — verify with
`grep -rn "log_action(" serve/`:

| Action | Written by |
|---|---|
| `feedback_submitted` | `POST /api/feedback` |
| `containment_decision_recorded` | `POST /api/trace/containment/decisions` — records the model suggestion and human decision, but excludes the free-text validation note from the general audit payload |
| `whatif_simulation_run` | `POST /api/whatif` |
| `alert_acknowledged` | `POST /api/alerts/{id}/acknowledge` |
| `recommendation_approved` / `recommendation_rejected` | `POST /api/recommendations/{id}/approve` / `reject` (ADR-069; replaces the retired ADR-051 `proposal_*` events, ADR-073) |
| `triage_run_started` | `POST /api/triage/runs` — payload records `plant_id`, the capped `acting_role`, the mode, the `intent`, and the `thread_id`/`turn_index` so a whole conversation is reconstructable from the audit trail |

### The GenAI tier's security posture (ADR-037)

`genai/` is a separate deployable. `serve/` never imports it, and it never holds a
database handle — it reads through the same HTTP API a human uses, so its entire
vocabulary is the ten functions in `genai/tools.py`.

**Privilege capping.** `serve/routers/triage.py::_cap_role` caps the role a run acts as at
the role that triggered it. Without it an agent is a read-around for the RBAC gate: a
`manager` could start a run acting as `leadership` and read economics they cannot read
directly. Capping turns a restricted run into a feature — the agent reports what it could
not read in `unanswered` instead of estimating it.

**The one named exception (ADR-066).** A signed-in supervisor's `incident` or `handover` run
acts as `manager`, not at the supervisor's own role: capped, it could not read the shift
summary, proposals or experiments, and a measured handover came back two claims long. It
is bounded so it is not a read-around: two fixed intents, the question replaced by the
intent's label (no operator prose reaches the model), no thread continuation, and a
supervisor reading the run gets the verified finding without the raw tool results. An
anonymous visitor to the public `/supervisor` view is refused with 401. The audit row
records `acting_role: manager` against the supervisor who started it.

**Agent actions are audited and distinguishable.** Every tool call carries
`X-PulseTwin-Actor: genai_triage/<run_id>` alongside `X-PulseTwin-Role`. The one non-GET
tool (`POST /api/whatif`) writes a `whatif_simulation_run` audit row, so an agent's
scenario tests appear in the IATF 16949 trail attributed to the role it ran as, never
anonymously.

**Data egress is an allowlist, not "whatever the agent asked for."** The tool registry is
ten plant-level aggregates. Unit-level surfaces (`/api/units/{id}`,
`/api/trace/containment/{id}`) and anything reading `checklists` are excluded — shipping
per-unit genealogy or operator-bucketed rows to a third-party API is the worst
data-governance step this feature could take, and the strongest operator-privacy defence
is that the data never leaves. `tests/test_genai_tools.py` asserts the registry's path set
*equals* the approved list, so widening egress requires editing a test.

**Prompt injection through data.** Tool results are model input, and in a real plant
`station_name` and `notes_code` are attacker-influenceable. Three mitigations: the
allowlist excludes free-text-heavy endpoints; the system prompt declares tool results to
be data, not instructions; and structurally, `genai/verifier.py` never reads the narrative
for facts, so an injected instruction cannot manufacture a *verified* number. It can still
manufacture prose — which is why the verifier redacts any sentence carrying an uncited
figure.

### The agent's authority (ADR-037, ADR-038, ADR-039, ADR-051)

The copilot is **read-only by construction**, not by policy. Its entire vocabulary is the
twenty entries in `genai/tools.py::build_registry`, and nineteen are GETs; the one POST
targets `/api/whatif`, a precomputed simulator that changes nothing. There is no tool that
can promote a model, edit a config, acknowledge an alert or stop the line, so "the agent
was instructed to do X" has no path to X — `dispatch` answers an unknown tool name with
`unknown_tool` and a list of what exists.

Four boundaries, each with a test:

| Boundary | Mechanism | Test |
|---|---|---|
| Privilege | `_cap_role` caps an agent's acting role at the caller's own, so a run can never read more than the human who started it | `tests/test_triage_api.py` |
| Egress | the tool registry IS the allowlist; its path set is asserted equal to a list written longhand | `tests/test_genai_tools.py` |
| Path injection | path parameters are URL-quoted with `safe=""`, so a unit id cannot add a segment and walk out of the allowlist | `tests/test_prompt_injection.py` |
| Grounding | a claim must resolve to a number in the recorded transcript; injected prose can manufacture a sentence, never a verified figure | `tests/test_prompt_injection.py` |

**Proposing is not doing (ADR-051, ADR-069).** An agent may return `proposed_actions`
from a closed catalogue of six, each citing the claims that justify it; the verifier
serves an action only if every cited claim verified. Deciding a recommendation is a
separate, human-called endpoint — `POST /api/recommendations/{id}/approve|reject` (the
Incident Commander's Decision Queue; the ADR-051 proposal endpoints were retired, ADR-073),
manager and above, audited, re-checked by the governor — and it is deliberately **not** in the tool
registry, so no instruction to an agent can reach it. An approved action changes only the
running *simulation* (`serve/live_simulation.py::MITIGATIONS`); nothing writes toward MQTT
or any OT interface, so the one-way OT→IT boundary is unchanged.

**Retrieved data is DATA.** Tool results reach the model as `role: "tool"` messages
carrying our own runner-assigned `call_id`, never as system or assistant turns. An
instruction embedded in a station name or an assumptions string cannot become authority,
and following one cannot produce a served number — which is the guarantee this tier
actually makes. It does not claim the model ignores such text; that is a property of the
model and is not assertable.

**Chain of thought is never surfaced.** `openai/gpt-oss-120b` returns a `reasoning` field.
The progress heartbeat is assembled from an explicit key allowlist
(`genai/triage.py::_HEARTBEAT_KEYS`), and `tests/test_genai_progress.py` drives the loop
with a stub whose content *and* reasoning carry a magic string, asserting it appears in no
heartbeat. The file is written to disk and rendered in a browser, so this is a disclosure
boundary rather than a UX preference.

**Credential handling.** `GROQ_API_KEY` is read from the environment only.
`GET /api/triage/availability` reports presence, never the value, a prefix, or its length.

**It is not ASGI middleware, despite the directory name.** The finals brief asked for
audit middleware; what is built is an explicit `log_action()` call at the end of each
mutating handler. That is deliberate — middleware fires on the request, before the handler
decides whether the action actually succeeded, so a rejected promotion or a failed
what-if would land in the audit trail as though it had happened. The trade-off is that a
**new mutating endpoint must remember to call it**; there is no framework-level net. If
you add one, add the `log_action()` call in the same commit.

The audit log is queryable at `GET /api/audit/log` (`leadership`/`system` only — CLAUDE.md's
operator-privacy and sourced-economics contracts extend to *who can see the audit trail
at all*), filterable by role/action/time range, and rendered live in
`frontend/components/ops/AuditLogViewer.tsx`. `frontend/components/shared/AuditToast.tsx`
fires a visible "✓ Action logged" confirmation at every mutating call site, so a judge can
watch the trail being written rather than take it on faith.

**Payload sanitisation:** logged payloads carry IDs and outcomes (`unit_id`, `verdict`,
`job_id`, `model_id`), never raw sensor readings or operator identity — the same
group-bucketing rule CLAUDE.md's operator-privacy contract already enforces on the
containment endpoint applies here too.

**Immutability:** `audit_log` is `INSERT`-only from application code — no `UPDATE`/`DELETE`
statement targeting it exists anywhere in `serve/` or `mlops/`. A production deployment
would additionally revoke `UPDATE`/`DELETE` grants on the table at the database-role
level and ship it to a separate log sink (see Data Governance's 7-year retention) rather
than relying on the absence of application code as the only guarantee.

## Data encryption

| | Local stack (`make up`) | Free-tier cloud (Render + Neon + Vercel) |
|---|---|---|
| In transit | Plaintext HTTP/MQTT (localhost, Docker network) | HTTPS terminated by Render and Vercel; `sslmode=require` on every Neon connection. MQTT is not deployed to the cloud |
| At rest | Postgres volume on the laptop, unencrypted | Neon encrypts storage at rest |

## Network segmentation

The design: `ingestion` is the only component that can reach MQTT; `serve` (backend)
reaches Postgres and Redis but never the broker; everything is default-deny-egress. In
this repo only the code half is enforced — `serve/` has no MQTT client and cannot import
one (the import wall). The network half was drafted as a Kubernetes NetworkPolicy that was
never applied and has been removed (ADR-054): on a real plant network, segmentation must
be enforced at deploy time by the network team, and this document is not that control.

## Secrets management

**Demo:** plaintext environment variables in `docker-compose.prod.yml` (`pulsetwin_dev`
placeholder password) — acceptable only because nothing here is a real credential and the
stack is never exposed outside localhost.

**Cloud:** secrets are entered in the Render dashboard (`sync: false` in `render.yaml`,
so they are never in the repo): `DATABASE_URL`, `PULSETWIN_GENAI_API_KEY`,
`PULSETWIN_CORS_ORIGINS`. Locally the GenAI key lives in `.env.local`, which is gitignored
and read by the stack at start-up.

## Vulnerability management

Not built for the demo (no CI pipeline exists to run it in), but the shape is: container
image scanning (Trivy/Grype) on every `serve/Dockerfile` and `ingestion/Dockerfile` build,
and dependency auditing (`pip-audit` against `serve/requirements.txt` /
`ingestion/requirements.txt`) on a schedule — both are a natural fit for the CI step this
repo does not currently have, not a redesign of anything that exists.

## Incident response

See `docs/INCIDENT_RUNBOOK.md` for the actual procedures (model drift, sensor dropout,
database failure, complete outage, false-alarm spike). This document states the posture;
that one states what a human does at 2 a.m.

## Compliance

- **IATF 16949** (automotive quality management) requires traceable evidence for quality
  decisions. The audit trail above, plus `docs/DECISIONS.md`'s ADR record of every
  modelling and threshold decision, is the traceability artifact — every alert, every
  acknowledgement, every model promotion has a timestamped, attributable record.
- **GDPR-adjacent operator data handling.** No real personal data is collected — operator
  identity is bucketed to an opaque group at the API boundary and never stored
  individually (CLAUDE.md's operator-privacy contract, ADR-017). `docs/DATA_GOVERNANCE.md`
  covers retention and the right-to-deletion process for the shift/team-level aggregates
  that do exist.
