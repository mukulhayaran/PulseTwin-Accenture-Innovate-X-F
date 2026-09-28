// All API access, typed. Static export means no server -- everything is client-side.

export interface StationCfg {
  station_id: number;
  station_name: string;
  zone: string;
  zone_name: string;
  sensor_tier: "full" | "manual";
  nominal_cycle_s: number;
  buffer_capacity: number;
  sensors: string[];
  is_inspection: number;
  position: number;
}

export interface Zone { id: string; name: string; stations: number[] }

export interface StreamEvent {
  seq?: number;
  run_id?: string;
  plant_id?: string;
  source?: "replay" | "live_synthetic" | "real";
  event_type?: string;
  scenario?: string;
  ts: string;
  unit_id: string;
  station_id: number;
  variant: string;
  lot_id: string;
  cycle_time_s: number;
  /** When this unit leaves the station. Absent on some live-synthetic events. */
  exit_ts?: string;
  /** The station's state BEFORE it took this unit (build_artifact writes prior_state). */
  station_state: string;
  sensor_tier: "full" | "manual";
  /** Null until the unit has left the prediction-cutoff station (ADR-063). */
  calibrated_risk: number | null;
  prediction_ts?: string | null;
  lead_time_min?: number | null;
  alert: number;
  provenance: Record<string, string>;
  alerts_used?: number;
  alerts_budget?: number;
  checklist_time_s?: number;
  checklist_pass?: number;
  notes_code?: string;
  torque_nm?: number;
  vibration_mm_s?: number;
  temperature_c?: number;
  humidity_pct?: number;
  film_thickness_um?: number;
  weld_current_a?: number;
  actual_defect?: number;
}

export interface UnitFactor {
  rank: number; feature: string; direction: string;
  contribution: number; value: number | null; plain_language: string;
}

export interface UnitDetail {
  unit_id: string; variant: string; lot_id: string; shift_id: string;
  calibrated_risk: number; ci_low: number; ci_high: number;
  alert_fired: number; above_cost_threshold: number;
  actual_defect: number | null; defect_mode: string | null; lead_time_min: number;
  current_station?: number | null;
  factors: UnitFactor[];
  journey: { station_id: number; entry_ts: string; exit_ts: string | null; cycle_s: number; station_state: string }[];
  readings: { station_id: number; signal: string; value: number; provenance: string; ci_low: number | null; ci_high: number | null }[];
}

export type ContainmentAction = "HOLD" | "INSPECT" | "MONITOR";

export interface ContainmentUnit {
  unit_id: string;
  current_station: number | null;
  risk: number;
  shared_factors: string[];
  explanation: string;
  /** INSPECTED: already through final inspection as of the view's clock -- see the outcome. */
  action: ContainmentAction | "INSPECTED";
  /** 1 defect / 0 clean, only once the vehicle has left final inspection. */
  inspection_outcome?: number | null;
  manual_action: ContainmentAction | null;
  decision_recorded_at: string | null;
  decision_status?: string | null;
  actual_outcome?: number | null;
  outcome_at?: string | null;
  validation_result?: string | null;
}

export interface Containment {
  flagged_unit: string; flagged_risk: number; flagged_factors: string[];
  at_risk_units: ContainmentUnit[];
  containment_window_min: number; total_at_risk: number;
}

export interface ContainmentDecision {
  stored: boolean;
  plant_id: string;
  flagged_unit_id: string;
  target_unit_id: string;
  recommended_action: ContainmentAction;
  selected_action: ContainmentAction;
  decided_by_role: string;
  decided_by_user: string;
  created_at: string;
  status: string;
  run_id: string | null;
}

export interface ContainmentDecisionHistory extends ContainmentDecision {
  id: number;
  message: string;
  owner: string | null;
  due_station: number | null;
  due_at: string | null;
  risk_at_decision: number;
  validation_result: string | null;
  actual_outcome: number | null;
  shared_factors: string[];
}

export type SimulationScenario =
  | "normal"
  | "torque_degradation"
  | "bad_material_lot"
  | "high_humidity"
  | "rushed_checklist"
  | "sensor_dropout"
  | "station_breakdown"
  | "buffer_congestion";

export interface SimulationRun {
  run_id: string;
  plant_id: string;
  seed: number;
  scenario: SimulationScenario;
  status: "running" | "paused" | "stopped" | "completed" | "failed";
  speed: number;
  severity: number;
  affected_station: number | null;
  affected_lot: string | null;
  incident_start_s: number;
  simulation_time: string | null;
  event_count: number;
  alerts_count: number;
  units_in_progress: number;
  source: "live_synthetic" | "real";
  producer: "backend" | "mqtt";
  started_at: string;
  ended_at: string | null;
  error: string | null;
}

export interface SimulationMetrics {
  run_id: string;
  events: number;
  alerts: number;
  active_alerts: number;
  correct_alerts: number;
  false_alerts: number;
  alert_correctness_rate: number | null;
  acknowledged: number;
  mean_time_to_acknowledge_s: number | null;
  mean_time_to_contain_s: number | null;
  decisions: number;
  validated_decisions: number;
  confirmed_defects: number;
  unnecessary_inspections: number;
  estimated_escaped_defects_avoided: number;
  estimated_cost_avoided: { value: number; currency: string; source: string };
  validation: Record<string, number>;
}

export interface Bottleneck {
  current_station: number; current_station_name: string; confidence: number;
  since: string; previous_station: number | null; shifted_at: string | null;
  detection_accuracy: number | null;
}

export interface Metrics {
  plant_id: string;
  values: Record<string, number>;
  reliability: { bin: number; predicted: number; observed: number; n: number; ci_low: number; ci_high: number }[];
  alarm_budget_curve: { threshold: number; alerts_per_shift: number; precision: number; recall: number }[];
  permutation_importance: { feature: string; delta_pr_auc: number }[];
}

let cachedBase: string | null = null;

// --------------------------------------------------------------------- role + session
//
// Finals brief Phase F4: a lightweight RBAC header, not full OAuth (docs/SECURITY.md has
// the OIDC upgrade path). Two separate things live here and conflating them is the bug
// this split exists to prevent:
//
//   session  -- WHO the server says you are. Established only by /api/auth/login, which
//               returns an opaque token (serve/session_store.py). Null means anonymous.
//   viewRole -- WHICH persona the mounted page is speaking as. A page pins its own
//               (usePersonaAccess), and /ops lets a demo drop it live. It can never
//               exceed the session's role -- see setRole() below.
//
// Anonymous resolves to "supervisor", never to "leadership" as it did before sessions
// existed: an unauthenticated visitor must get the LEAST access this API grants, matching
// serve/middleware/auth.py's own default. That is what makes /overview degrade for a
// signed-out visitor instead of quietly handing them the leadership panels.
export type Role = "supervisor" | "manager" | "leadership" | "system";

// Mirrors serve/middleware/auth.py's ROLE_ORDER hierarchy -- each role can do everything
// the one before it can. That hierarchy governs what the API ALLOWS; it is no longer what
// governs which page you may open, because a hierarchy cannot express "manager and
// leadership are peers who do not see each other's view" -- lib/routeAccess.ts owns that
// and it is an exact-match policy, not a rank comparison.
//
// Declared before the session block below because readStoredSession() validates against
// it at module-init time, and a `const` referenced before its own initialiser is a
// temporal-dead-zone throw, not a hoist.
export const ROLE_ORDER: Role[] = ["supervisor", "manager", "leadership", "system"];
export function roleAtLeast(role: Role, min: Role): boolean {
  return ROLE_ORDER.indexOf(role) >= ROLE_ORDER.indexOf(min);
}

export interface Session {
  role: Role;
  token: string;
  user: string;
  /** Unix seconds, from the server -- not a client clock reading. */
  expires_at: number;
}

// Persisted in sessionStorage, which is a deliberate and narrow exception to CLAUDE.md
// §7's "no localStorage/sessionStorage" rule (ADR-048). That rule is about UI state that
// must be DERIVED rather than remembered; a sign-in is not derived state. Without it the
// module variable resets on every reload, and since this ships as a static export, typing
// /manager into the address bar is a reload -- so every hard navigation would sign the
// user out and put them back behind the sign-in gate. sessionStorage rather than localStorage so the
// session ends with the tab, and the value is a server-issued token that the server
// re-validates (restoreSession) rather than a role string the client can edit into
// whatever it likes.
const SESSION_KEY = "pulsetwin.session";

function readStoredSession(): Session | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.sessionStorage.getItem(SESSION_KEY);
    if (!raw) return null;
    const s = JSON.parse(raw) as Session;
    if (!s?.token || !ROLE_ORDER.includes(s.role)) return null;
    if (typeof s.expires_at === "number" && s.expires_at * 1000 <= Date.now()) return null;
    return s;
  } catch {
    return null;
  }
}

function writeStoredSession(s: Session | null): void {
  if (typeof window === "undefined") return;
  try {
    if (s) window.sessionStorage.setItem(SESSION_KEY, JSON.stringify(s));
    else window.sessionStorage.removeItem(SESSION_KEY);
  } catch {
    /* private-mode or blocked storage: the in-memory session below still works for
       this page view, it just will not survive a reload. Never fatal. */
  }
}

let session: Session | null = readStoredSession();

/** Fired on sign-in and sign-out, so a mounted page's access check re-runs in place. */
export const SESSION_EVENT = "pulsetwin:session";
function announceSession(): void {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(SESSION_EVENT));
}
let viewRole: Role | null = null;

export function getSession(): Session | null {
  return session;
}

/** The role every request declares: the page's pinned persona, else the session's. */
export function getRole(): Role {
  return viewRole ?? session?.role ?? "supervisor";
}

/**
 * Pin the acting persona for the mounted page.
 *
 * Clamped to the session: a page may ask to speak as a role at or below what the server
 * granted (that is /ops's "Viewing as" demo, and /copilot pinning `manager`), and a
 * request for anything above it is ignored rather than honoured. Before sessions this
 * function was the only thing deciding the role header, so calling it WAS the privilege
 * grant -- which is exactly why an anonymous visitor could reach leadership data.
 */
export function setRole(role: Role): void {
  const ceiling: Role = session?.role ?? "supervisor";
  viewRole = roleAtLeast(role, ceiling) ? ceiling : role;
}

// serve/routers/auth_demo.py -- checked server-side (not a client-JS credential check
// anyone could read out of the bundle) and now issuing a session token the API validates
// on every request that carries it.
export interface LoginResult {
  ok: true;
  role: Role;
  token: string;
  user: string;
  expires_at: number;
}

export async function login(username: string, password: string): Promise<Session> {
  const r = await post<LoginResult>("/api/auth/login", { username, password });
  session = { role: r.role, token: r.token, user: r.user, expires_at: r.expires_at };
  viewRole = null;
  writeStoredSession(session);
  announceSession();
  return session;
}

/** End the session server-side too, so the token stops working, not just this tab. */
export async function logout(): Promise<void> {
  const token = session?.token;
  session = null;
  viewRole = null;
  writeStoredSession(null);
  announceSession();
  if (!token) return;
  try {
    const base = await apiBase();
    await fetch(`${base}/api/auth/logout`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-PulseTwin-Token": token },
    });
  } catch {
    /* Signed out locally regardless -- a token the server never heard about expires on
       its own, and refusing to clear the client state on a network blip would strand the
       user in a session they explicitly ended. */
  }
}

/**
 * Re-validate a restored token against the server. Returns the live session or null.
 *
 * This is what makes expiry and sign-out real rather than advisory: a token sitting in
 * sessionStorage proves nothing until the server says it still resolves to a role.
 */
export async function restoreSession(): Promise<Session | null> {
  const stored = session;
  if (!stored) return null;
  try {
    const base = await apiBase();
    const r = await fetch(`${base}/api/auth/session`, {
      headers: { "X-PulseTwin-Token": stored.token },
    });
    if (!r.ok) {
      session = null;
      viewRole = null;
      writeStoredSession(null);
      return null;
    }
    const body = (await r.json()) as { role: Role; user: string; expires_at: number };
    session = { ...stored, role: body.role, user: body.user, expires_at: body.expires_at };
    writeStoredSession(session);
    return session;
  } catch {
    // The API being unreachable is not proof the session is bad. Keep it and let the
    // OfflineBanner say what is actually wrong, rather than signing the user out
    // because the backend restarted.
    return stored;
  }
}

function authHeaders(): Record<string, string> {
  const role = getRole();
  const headers: Record<string, string> = {
    "X-PulseTwin-Role": role,
    "X-PulseTwin-User": session?.user ?? `demo-${role}`,
  };
  if (session) headers["X-PulseTwin-Token"] = session.token;
  return headers;
}

// Runtime config, not build-time (ADR-028). Repointing the UI at a different backend
// must not require rebuilding a static export ten minutes before a demo.
export async function apiBase(): Promise<string> {
  if (cachedBase) return cachedBase;
  try {
    const r = await fetch("/config.json", { cache: "no-store" });
    if (r.ok) {
      const j = await r.json();
      if (j?.apiBase) { cachedBase = j.apiBase as string; return cachedBase; }
    }
  } catch { /* fall through to the env default */ }
  cachedBase = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:7860";
  return cachedBase;
}

async function get<T>(path: string): Promise<T> {
  const base = await apiBase();
  const r = await fetch(`${base}${path}`, { headers: authHeaders() });
  if (!r.ok) throw new Error(`${path} -> ${r.status}`);
  return r.json() as Promise<T>;
}

export const getStations = (plant: string) =>
  get<{ stations: StationCfg[]; zones: Zone[] }>(`/api/config/stations?plant_id=${plant}`);
export const getHealth = () => get<any>("/api/health");
function temporalQuery(plant: string, atTs?: string | null, runId?: string | null): string {
  const q = new URLSearchParams({ plant_id: plant });
  if (atTs) q.set("at_ts", atTs);
  if (runId) q.set("run_id", runId);
  return q.toString();
}

export const getUnit = (id: string, plant: string, atTs?: string | null, runId?: string | null) =>
  get<UnitDetail>(`/api/units/${id}?${temporalQuery(plant, atTs, runId)}`);
export const getContainment = (id: string, plant: string, atTs?: string | null, runId?: string | null) =>
  get<Containment>(`/api/trace/containment/${id}?${temporalQuery(plant, atTs, runId)}`);
export const recordContainmentDecision = (
  plant: string,
  flaggedUnitId: string,
  targetUnitId: string,
  selectedAction: ContainmentAction,
  message: string,
  runId?: string | null,
  atTs?: string | null,
  owner?: string | null,
  dueStation?: number | null,
  dueAt?: string | null,
) => post<ContainmentDecision>("/api/trace/containment/decisions", {
  plant_id: plant,
  flagged_unit_id: flaggedUnitId,
  target_unit_id: targetUnitId,
  selected_action: selectedAction,
  message,
  run_id: runId,
  alert_id: runId ? `${runId}:${targetUnitId}` : null,
  at_ts: atTs,
  owner: owner?.trim() || null,
  due_station: dueStation ?? null,
  due_at: dueAt || null,
});
export const getContainmentDecisions = (plant: string, runId?: string | null) => {
  const q = new URLSearchParams({ plant_id: plant, limit: "50" });
  if (runId) q.set("run_id", runId);
  return get<ContainmentDecisionHistory[]>(`/api/trace/containment-decisions?${q.toString()}`);
};
export const getBottleneck = (plant: string) =>
  get<Bottleneck>(`/api/bottleneck/current?plant_id=${plant}`);
export const getMetrics = (plant: string) => get<Metrics>(`/api/metrics?plant_id=${plant}`);
export const getStationStats = (plant: string) => get<any[]>(`/api/stations/stats?plant_id=${plant}`);
export const getRoi = (plant: string, scenario = "base") =>
  get<any>(`/api/economics/roi?plant_id=${plant}&scenario=${scenario}`);

export async function streamUrl(
  plant: string,
  speed: number,
  fromTs?: string | null
): Promise<string> {
  const base = await apiBase();
  // from_ts seeks via the stream_index table (minute granularity), so resuming after a
  // pause continues the shift rather than restarting it. Omitted entirely when absent --
  // an empty from_ts would seek to offset 0 and silently replay from the beginning.
  // A fresh start (no resume point) skips the shift's quiet opening and begins just
  // before its first alert, so the alert feed is not empty for ~5 minutes at 10x.
  const seek = fromTs ? `&from_ts=${encodeURIComponent(fromTs)}` : "&start=first_alert";
  return `${base}/api/stream?plant_id=${plant}&speed=${speed}${seek}`;
}

export const getActiveSimulation = (plant: string) =>
  get<SimulationRun | null>(`/api/simulations/active?plant_id=${encodeURIComponent(plant)}`);
export const getSimulation = (runId: string) =>
  get<SimulationRun>(`/api/simulations/${encodeURIComponent(runId)}`);
export const getSimulationMetrics = (runId: string) =>
  get<SimulationMetrics>(`/api/simulations/${encodeURIComponent(runId)}/metrics`);

export const startSimulation = (
  plant: string,
  scenario: SimulationScenario,
  seed: number,
  speed: number,
  severity = 0.7,
  affectedStation?: number | null,
  affectedLot?: string | null,
  incidentStartS = 0,
) => post<SimulationRun>("/api/simulations", {
  plant_id: plant,
  scenario,
  seed,
  speed,
  severity,
  affected_station: affectedStation ?? null,
  affected_lot: affectedLot?.trim() || null,
  incident_start_s: incidentStartS,
  max_events: 5000,
});

export const injectSimulationIncident = (
  runId: string,
  scenario: SimulationScenario,
  severity: number,
  affectedStation?: number | null,
  affectedLot?: string | null,
) => post<SimulationRun>(`/api/simulations/${encodeURIComponent(runId)}/incidents`, {
  scenario,
  severity,
  affected_station: affectedStation ?? null,
  affected_lot: affectedLot?.trim() || null,
});

export const pauseSimulation = (runId: string) =>
  post<SimulationRun>(`/api/simulations/${runId}/pause`, {});
export const resumeSimulation = (runId: string) =>
  post<SimulationRun>(`/api/simulations/${runId}/resume`, {});
export const stopSimulation = (runId: string) =>
  post<SimulationRun>(`/api/simulations/${runId}/stop`, {});

export async function liveSimulationStreamUrl(runId: string, afterSeq = 0): Promise<string> {
  const base = await apiBase();
  return `${base}/api/simulations/${encodeURIComponent(runId)}/stream?after_seq=${afterSeq}`;
}

// ---------------------------------------------------------------- manager / leadership

export const getWhatIfStations = (plant: string) =>
  get<{ station_id: number; station_name: string; sensor_tier: string }[]>(
    `/api/whatif/stations?plant_id=${plant}`
  );

export const getInstrumentationPlan = (plant: string) =>
  get<any>(`/api/economics/instrumentation-plan?plant_id=${plant}`);

export async function getWhatIf(
  plant: string, stationId: number, bufferDelta: number, cycleDeltaPct: number
) {
  const base = await apiBase();
  const r = await fetch(`${base}/api/whatif`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({
      plant_id: plant, station_id: stationId,
      buffer_delta: bufferDelta, cycle_delta_pct: cycleDeltaPct,
    }),
  });
  if (!r.ok) throw new Error(`whatif -> ${r.status}`);
  return r.json();
}

// ------------------------------------------------------------------------ ops (F6)

// Only the fields AiTrustPanel actually reads are typed. The endpoint returns more, and
// typing the rest as `any` would let a renamed field fail silently at runtime on the one
// panel whose entire job is being trustworthy.
export interface ModelHealthLike {
  current_model?: { id?: string; status?: string; age_days?: number | null };
  drift?: { overall_status?: string };
  fallback?: { status?: string };
  recommendation?: string;
}

export const getModelHealth = (plant: string) =>
  get<ModelHealthLike>(`/api/model/health?plant_id=${plant}`);
export const getSystemHealth = (plant: string) => get<any>(`/api/system/health?plant_id=${plant}`);
// Supervisor-gated slice of the same dropout list. /supervisor must NOT call
// getSystemHealth: that endpoint requires `manager`, so a page that pins itself to
// `supervisor` got a silent 403 on every poll and never showed a dropout.
export const getSensorDropouts = (plant: string) =>
  get<{ sensor_dropouts_active: number; dropout_details: any[] }>(
    `/api/system/sensor-dropouts?plant_id=${plant}`
  );
export const getIngestionLag = (plant: string) =>
  get<any>(`/api/system/ingestion-lag?plant_id=${plant}`);
export const getAuditLog = (params: {
  role?: string; action?: string; from?: string; to?: string; limit?: number;
} = {}) => {
  // Drop undefined/empty entries BEFORE building the query string. `new URLSearchParams
  // ({role: undefined})` stringifies the value, producing the literal `role=undefined` --
  // which the backend then matched as `WHERE user_role = 'undefined'`, so AuditLogViewer
  // with no filters selected returned zero rows against a table with entries in it.
  const clean = Object.fromEntries(
    Object.entries(params)
      .filter(([, v]) => v !== undefined && v !== null && v !== "")
      .map(([k, v]) => [k, String(v)])
  );
  const q = new URLSearchParams(clean).toString();
  return get<{ entries: any[]; count: number }>(`/api/audit/log${q ? `?${q}` : ""}`);
};
export const getAuditActions = () => get<{ actions: string[] }>("/api/audit/actions");

// ----------------------------------------------------------------- alerts / escalation

export const getEscalationPolicy = () => get<{
  levels: { level: number; name: string; response_window_min: number | null }[];
  containment_escalation: { at_risk_threshold: number; skip_to_level: number };
}>("/api/alerts/escalation-policy");

export const acknowledgeAlert = (unitId: string, plant: string, runId?: string | null) =>
  post<{ acknowledged: boolean; unit_id: string; by_role: string; acknowledged_at: string }>(
    `/api/alerts/${unitId}/acknowledge`, { plant_id: plant, run_id: runId }
  );

export interface AlertStatus {
  alert_id: string;
  status: "OPEN" | "ACKNOWLEDGED" | "ACTIONED" | "VALIDATED" | "CLOSED";
  opened_at: string;
  acknowledged_at: string | null;
  actioned_at: string | null;
  validated_at: string | null;
  closed_at: string | null;
  outcome: number | null;
  outcome_at: string | null;
  /** Set the moment this alert auto-triggered an unattended "incident" investigation
   *  (serve/live_simulation.py::_trigger_auto_investigation). Feed it to getTriageRun()
   *  to render the same FindingView a manually-started run uses. Null on the static
   *  replay path (/api/stream), which has no live-simulation run_id to key off. */
  triage_run_id: string | null;
}

export const getAlertStatus = (unitId: string, plant: string, runId?: string | null) => {
  const q = new URLSearchParams({ plant_id: plant });
  if (runId) q.set("run_id", runId);
  return get<AlertStatus>(`/api/alerts/${encodeURIComponent(unitId)}/status?${q.toString()}`);
};

export const closeAlert = (unitId: string, plant: string, runId: string) =>
  post<{ closed: boolean; unit_id: string; status: "CLOSED"; closed_at: string }>(
    `/api/alerts/${encodeURIComponent(unitId)}/close`, { plant_id: plant, run_id: runId }
  );

// The registered models for a plant, read-only (ADR-053: no retraining) -- ModelHealthPanel
// reads the committed champion's measured metrics from here.
export const getModelRegistry = (plant: string, modelType = "defect_risk") =>
  get<any>(`/api/model/registry?plant_id=${plant}&model_type=${modelType}`);

async function post<T>(path: string, body: unknown): Promise<T> {
  const base = await apiBase();
  const r = await fetch(`${base}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${path} -> ${r.status}`);
  return r.json() as Promise<T>;
}

// ------------------------------------------------------------------- agentic triage
//
// The agent runs in the genai/ tier, never in serve/. These endpoints only start a run
// and read the VERIFIED result -- `finding_raw` is deliberately never served, because a
// redacted claim that reaches a screen has not been redacted.

export interface TriageCitation { call_id: string; pointer: string }

export interface TriageClaim {
  id: string;
  text: string;
  value: number;
  unit: string;
  provenance: "measured" | "inferred";
  citation: TriageCitation;
  ci_low: number | null;
  ci_high: number | null;
  source: string | null;
}

export interface TriageFinding {
  question: string;
  summary: string;
  claims: TriageClaim[];
  recommendation: string;
  alternatives_considered: string[];
  confidence: "low" | "medium" | "high";
  unanswered: string[];
  // ADR-046: verbatim excerpts from /api/knowledge/search, each substring-checked by the
  // verifier -- the retrieval half of a copilot answer.
  knowledge_citations?: TriageKnowledgeCitation[];
  // ADR-051: served only when every claim they cite verified.
  proposed_actions?: ProposedAction[];
  // ADR-064: why the investigation stopped, and the bounded hypotheses it tested.
  decision?: FindingDecision | "";
  hypotheses?: FindingHypothesis[];
}

export type FindingDecision =
  | "recommend" | "insufficient_evidence" | "unsupported_intervention"
  | "simulator_unavailable" | "no_improvement" | "too_uncertain" | "engineering_review";

export interface FindingHypothesis {
  id: string;
  statement: string;
  status: "supported" | "rejected" | "untested";
  claim_ids: string[];
  experiment_id?: string | null;
}

export type ActionType =
  | "increase_feed_buffer" | "rebalance_cycle" | "shift_weld_load"
  | "maintenance_ticket" | "hvac_setpoint" | "resequence_paint_queue";

export interface ProposedAction {
  id: string;
  action_type: ActionType;
  params: Record<string, number | string>;
  rationale: string;
  claim_ids: string[];
}

export interface TriageKnowledgeCitation {
  id: string;
  quoted_text: string;
  doc_id: string;
  section: string;
  citation: { call_id: string; pointer: string };
}

export interface TriageVerdict {
  claim_id: string;
  code: string;
  detail: string;
  resolved_value: number | null;
}

// The named actions the copilot offers. An intent is a KEY, not a canned English
// sentence: the deterministic path selects a plan on the same key with no language
// understanding at all, which is why every button still works with no provider
// credential. Mirrors genai/intents.py.
export type TriageIntent =
  | "ask" | "incident" | "handover" | "whatif";

export interface TriageToolEvent {
  call_id: string;
  tool_name: string;
  phase?: string;
  agent?: string;
  status?: "ok" | "running";
  duration_ms: number | null;
}

// The specialist roster for an orchestrated `incident` run (ADR-045). Absent/empty for
// every other intent and for the deterministic path.
export interface TriageAgentStatus {
  key: string;
  label: string;
  status: "pending" | "running" | "ok" | "partial" | "error";
  tool_calls: number;
}

export interface TriageProgress {
  phase: string | null;
  phase_index: number | null;
  iteration: number | null;
  max_iterations: number | null;
  elapsed_s: number | null;
  agent?: string | null;
  agents?: TriageAgentStatus[] | null;
}

export interface TriageRun {
  run_id: string;
  status: "unknown" | "queued" | "running" | "verified"
        | "verified_with_redactions" | "rejected" | "failed";
  mode?: "agent" | "deterministic";
  acting_role?: string;
  question?: string;
  intent?: TriageIntent;
  thread_id?: string;
  turn_index?: number;
  station_id?: number | null;
  capability_profile?: string | null;
  finding?: TriageFinding | null;
  verification?: {
    status: string;
    claims_total: number;
    claims_verified: number;
    claim_verdicts: TriageVerdict[];
    prose_issues: string[];
    reject_reason: string;
    carried_values_used?: number;
  } | null;
  tool_ledger?: TriageToolEvent[];
  progress?: TriageProgress;
  phase_order?: string[];
  claims_total?: number;
  claims_verified?: number;
  tool_calls?: number;
  latency_s?: number;
  cost_usd?: number;
  cost_source?: string;
  created_at?: string;
  error?: string;
  // ADR-045: set only for an orchestrated `incident` run.
  orchestrated?: boolean;
  agents_used?: TriageAgentStatus[] | null;
}

export interface TriageThread {
  thread_id: string;
  created_at: string;
  plant_id: string;
  title: string;
  opened_by: string;
  turn_count: number;
  last_run_at: string | null;
}

export interface TriageThreadDetail {
  thread: TriageThread;
  turns: TriageRun[];
  count: number;
  phase_order: string[];
}

export const getTriageAvailability = () =>
  get<{
    enabled: boolean;
    reason: string;
    mode: "agent" | "deterministic";
    intents: Record<TriageIntent, string>;
    phase_order: string[];
  }>("/api/triage/availability");

export interface StartTriageArgs {
  plant: string;
  question?: string;
  intent?: TriageIntent;
  threadId?: string | null;
  // ALWAYS structured, never parsed out of the question text: CLAUDE.md §1 bans station
  // literals, and regexing "S17" out of prose is how a copilot simulates the wrong
  // station with complete confidence.
  stationId?: number | null;
  actingRole?: Role;
}

export const startTriage = (args: StartTriageArgs) =>
  post<{
    run_id: string; status: string; mode: string; acting_role: string;
    thread_id: string; turn_index: number; intent: TriageIntent;
  }>("/api/triage/runs", {
    plant_id: args.plant,
    question: args.question ?? null,
    intent: args.intent ?? "ask",
    thread_id: args.threadId ?? null,
    station_id: args.stationId ?? null,
    acting_role: args.actingRole ?? null,
    mode: "auto",
  });

export const getTriageRun = (runId: string) =>
  get<TriageRun>(`/api/triage/runs/${runId}`);

/** `intent` filters on a thread's opening turn -- the copilot lists "ask" threads only. */
export const listTriageThreads = (plant: string, limit = 20, intent?: TriageIntent) =>
  get<{ threads: TriageThread[]; count: number }>(
    `/api/triage/threads?plant_id=${plant}&limit=${limit}${intent ? `&intent=${intent}` : ""}`
  );

// ------------------------------------------------------- ADR-064: experiments + outcomes
/** An interval over simulated seeds. Always provenance "inferred": never measured. */
export interface SimInterval {
  mean: number; ci_low: number; ci_high: number; p10: number; p90: number; n: number;
  provenance: "inferred";
}

export type SimMetric =
  | "throughput_uph" | "wip_mean" | "blocked_pct" | "starved_pct" | "down_pct"
  | "downtime_s" | "expected_defects" | "alerts";

export interface ExperimentScenario {
  scenario_id: string;
  label: string;
  intervention_type: string | null;
  params: Record<string, number | string>;
  is_baseline: boolean;
  supported: boolean;
  reason: string | null;
  simulated_as: string | null;
  seeds_ok: number;
  seeds_failed: number;
  metrics: Partial<Record<SimMetric, SimInterval>>;
  vs_baseline: (Partial<Record<SimMetric, SimInterval>> & { paired_seeds?: number }) | null;
  bottleneck_station: number | null;
  bottleneck_share: number | null;
  expected_value_usd_per_hour: (SimInterval & { source: string }) | null;
  intervention_cost_usd: number | null;
  intervention_cost_reason: string | null;
}

export interface SimulationExperiment {
  experiment_id: string;
  status: "queued" | "running" | "done" | "failed" | "cancelled" | "timeout";
  error: string | null;
  plant_id: string;
  station_id: number;
  objective: string;
  profile: string | null;
  origin_run_id: string | null;
  origin_proposal_id: string | null;
  created_at: string;
  runtime_s: number | null;
  simulator_version: string | null;
  method: "fresh_simulation";
  method_detail: string;
  horizon_s: number;
  seeds: number[];
  seed_count: number;
  incident: Record<string, unknown> | null;
  progress: { results_done: number; results_failed: number; results_total: number };
  verdict: "improved" | "too_uncertain" | "no_improvement" | "no_scenarios" | null;
  best_label: string | null;
  scenarios: ExperimentScenario[];
  assumptions: string[];
}

export interface ExperimentListItem {
  experiment_id: string; created_at: string; station_id: number; objective: string;
  status: SimulationExperiment["status"]; profile: string | null; seed_count: number;
  origin_run_id: string | null; origin_proposal_id: string | null;
  verdict: SimulationExperiment["verdict"]; best_label: string | null;
  runtime_s: number | null;
}

export const getSimulationExperiment = (id: string) =>
  get<SimulationExperiment>(`/api/simulation-experiments/${encodeURIComponent(id)}`);

export const listSimulationExperiments = (
  plant: string, opts: { originRunId?: string; originProposalId?: string; limit?: number } = {},
) => {
  const q = new URLSearchParams({ plant_id: plant, limit: String(opts.limit ?? 10) });
  if (opts.originRunId) q.set("origin_run_id", opts.originRunId);
  if (opts.originProposalId) q.set("origin_proposal_id", opts.originProposalId);
  return get<{ experiments: ExperimentListItem[]; count: number }>(
    `/api/simulation-experiments?${q.toString()}`);
};

export type OutcomeStatus =
  | "pending" | "observing" | "successful" | "partially_successful"
  | "no_material_effect" | "harmful" | "inconclusive" | "cancelled";

export interface ActionOutcome {
  outcome_id: string;
  proposal_id: string;
  experiment_id: string | null;
  station_id: number;
  intervention_type: string;
  status: OutcomeStatus;
  metric: string;
  direction: string;
  applied_at: string | null;
  window_s: number | null;
  window_ends_at: string | null;
  baseline_value: number | null;
  expected_change: number | null;
  expected_ci_low: number | null;
  expected_ci_high: number | null;
  expected_source: string | null;
  actual_value: number | null;
  actual_change: number | null;
  prediction_error: number | null;
  n_events_before: number | null;
  n_events_after: number | null;
  reason: string | null;
  evaluated_at: string | null;
}

export type LifecycleStage =
  | "investigating" | "forming_hypotheses" | "simulating" | "comparing" | "verifying"
  | "awaiting_approval" | "applied" | "monitoring" | "outcome_evaluated";

export interface Lifecycle {
  phase: LifecycleStage | null;
  reached: LifecycleStage[];
  terminal: string | null;
  stages: LifecycleStage[];
}

export const getActionOutcomes = (plant: string, limit = 20) =>
  get<{ outcomes: ActionOutcome[]; count: number;
        accuracy: { outcomes: number; evaluated: number; success_rate: number | null;
                    with_projection: number; mean_abs_projection_error: number | null } }>(
    `/api/learning/outcomes?plant_id=${plant}&limit=${limit}`);

export const getTriageThread = (threadId: string) =>
  get<TriageThreadDetail>(`/api/triage/threads/${threadId}`);

// ------------------------------------------------------- decision intelligence (ADR-038)
//
// Deterministic, computed in the backend. The copilot narrates these; it does not
// produce them, because a verifier can only check a claim against a tool result -- so
// the tool result has to be the trustworthy thing.

export interface TriageFactor {
  name: string;
  value: number | null;
  unit: string;
  informative?: boolean;
  note: string;
}

export interface AlertTriageRow {
  unit_id: string;
  prediction_ts: string;
  alert_fired: boolean;
  current_station: number | null;
  implicated_station: number | null;
  triage_class: "CRITICAL" | "INVESTIGATE" | "MONITOR" | "LOW_CONFIDENCE";
  risk: number | null;
  ci_low: number | null;
  ci_high: number | null;
  severity_x_threshold: number | null;
  lead_time_min: number | null;
  peers_above_threshold: number;
  units_sharing_fingerprint: number;
  reasons: string[];
  factors: TriageFactor[];
  provenance: "measured" | "inferred";
  rank: number;
}

export interface AlertTriage {
  plant_id: string;
  alerts: AlertTriageRow[];
  count: number;
  anchor_ts: string;
  anchor_source: "caller" | "replay_end_ts";
  already_fired: number;
  candidate_set: string;
  counts_by_class: Record<string, number>;
  budget_threshold: number;
  diagnostics: {
    distinct_risk_values: number;
    severity_discriminates: boolean;
    confidence_interval_is_derived: boolean;
    notes: string[];
  };
  method: string;
  thresholds: Record<string, number>;
}

export interface ShiftSummary {
  plant_id: string;
  shift: {
    label: string; window_start: string; window_end: string;
    anchor_ts: string; anchor_source: string;
  };
  production: {
    units: number; alerts_fired: number; alert_budget: number;
    confirmed_defects: number; mean_risk: number | null;
    units_per_hour_target: number | null; achieved_uph: number | null;
  };
  bottleneck: {
    windows_in_shift: number; shift_events: number;
    opened_at_station: number | null; closed_at_station: number | null;
    detector_agreement: number | null;
    moves: { at: string; to_station: number; confidence: number | null }[];
  };
  worst_stations: {
    station_id: number; lost_uph: number | null; util: number | null;
    provenance: string;
  }[];
  sensors: {
    dropouts_active: number; stations_affected: number[];
    detail: { station_id: number; sensor: string; since: string | null }[];
  };
  model: {
    drift_metrics_breached: number;
    breached: { metric_type: string; feature: string; value: number | null;
                threshold: number | null }[];
  };
  method: string;
}

export interface DataQualityStation {
  station_id: number; station_name: string | null; sensor_tier: string | null;
  readings: number; signals: number;
  completeness: number | null; timeliness: number | null; validity: number | null;
  overall: number | null; measured: number; inferred: number;
  provenance: "measured" | "inferred"; sensors_dark: string[];
}

export interface DataQuality {
  plant_id: string;
  stations: DataQualityStation[];
  count: number;
  plant_score: number | null;
  components_used: string[];
  components_unavailable: string[];
  notes: string[];
  method: string;
  weights: Record<string, number>;
}

export const getAlertTriage = (plant: string, limit = 50, atTs?: string) =>
  get<AlertTriage>(
    `/api/alerts/triage?plant_id=${plant}&limit=${limit}` +
      (atTs ? `&at_ts=${encodeURIComponent(atTs)}` : "")
  );

export const getShiftSummary = (plant: string) =>
  get<ShiftSummary>(`/api/shift/summary?plant_id=${plant}`);

export const getDataQuality = (plant: string) =>
  get<DataQuality>(`/api/system/data-quality?plant_id=${plant}`);



// ------------------------------------------------------------------ incident commander
//
// ADR-067. Every number these return was computed by the backend (probes, the twin, the
// governor); the UI renders them and never derives a confidence, a cost or a verdict.

async function postDetail<T>(path: string, body: unknown): Promise<T> {
  const base = await apiBase();
  const r = await fetch(`${base}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    let detail = `${r.status}`;
    try { detail = (await r.json())?.detail ?? detail; } catch { /* keep the status */ }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return r.json() as Promise<T>;
}

export interface Hypothesis {
  id: string; key: string; title: string; description: string;
  possible_causes: string[]; expected_observations: string[];
  supporting_evidence: string[]; contradicting_evidence: string[];
  confidence: number; prior: number; status: string; station_id: number | null;
  contributions: { evidence_id: string; delta: number }[];
}
export interface Evidence {
  evidence_id: string; specialist: string; probe: string; station_id: number | null;
  provenance: "measured" | "inferred" | "simulated" | "manual" | "missing";
  summary: string; observed_at: string | null; created_at: string;
  data: Record<string, any>; effects: Record<string, number>; quality: number;
}
export interface InvestigationStep {
  step_id: string; seq: number; kind: string; agent: string | null; summary: string;
  plan: Record<string, any> | null; plan_source: string | null; plan_valid: number | null;
  rejection_reason: string | null; created_at: string;
}
export interface Investigation {
  investigation_id: string; status: string; phase: string; planner_mode: string;
  created_at: string; finished_at: string | null; steps: InvestigationStep[];
  agent_runs: any[];
  state: {
    objective: string; hypotheses: Hypothesis[]; evidence: Evidence[];
    assessment: Record<string, any> | null; decision: string | null;
    experiments: any[]; sensor_maturity: Record<string, string>;
    model_health: Record<string, any> | null; current_step: Record<string, any> | null;
    budgets: Record<string, any>; fallbacks: any[]; trace: Record<string, any> | null;
    window?: Record<string, any>; stop_reason: string | null;
  };
}
export interface RecOption {
  option_id: string; action_type: string; title: string; station_id: number | null;
  parameters: Record<string, any>; adapter: string; risk: string; reversible: boolean;
  autonomy_level_required: string; expected_impact: Record<string, any> | null;
  cost: Record<string, any> | null; maintenance_plan: Record<string, any> | null;
  governor: { decision: string; required_level: string | null; granted_level: string;
              reasons: string[] };
  rank: number;
}
export interface Recommendation {
  recommendation_id: string; status: string; assessment: string;
  root_cause_key: string | null; root_cause_title: string | null; confidence: number;
  options: RecOption[]; rationale: { points: any[]; therefore: string; scoring: string } | null;
  decision_note: string | null; decided_by_role: string | null;
}
export interface IncidentSummary {
  incident_id: string; plant_id: string; kind: string; station_id: number | null;
  title: string; severity: string; source: string; status: string; created_at: string;
  investigation: Record<string, any> | null; recommendation: Record<string, any> | null;
  sim_run_id: string; trigger_ts: string;
  /** One plain-language line, built server-side from the incident's own record (ADR-070). */
  headline: string;
  /** Vehicles the incident touches (current-run list only; ADR-071). */
  affected?: { count: number | null; basis: string } | null;
  /** Earlier incidents with the same kind, station and trigger. */
  seen_before: number;
}
export interface IncidentDetail {
  incident: IncidentSummary & { notes: string | null; trigger_ts: string; sim_run_id: string };
  investigation: Investigation | null; recommendation: Recommendation | null;
  actions: any[]; case: Record<string, any> | null;
  headline: string;
  /** "In simple words": titled sections generated from this incident's stored data. */
  explanation: { title: string; text: string }[];
  /** Detect → … → Verify, derived server-side from the persisted record (LOOP G). */
  stages?: IncidentStage[];
}
export interface IncidentStage {
  key: string; label: string; status: "done" | "active" | "pending" | "skipped"; detail: string;
}

export const listIncidents = (plant: string, scope: "current" | "all" = "current", limit = 30) =>
  get<{ incidents: IncidentSummary[]; run: { run_id: string; status: string } | null }>(
    `/api/incidents?plant_id=${plant}&scope=${scope}&limit=${limit}`);
export const getIncident = (id: string) => get<IncidentDetail>(`/api/incidents/${id}`);
export const getSimilarCases = (id: string) => get<{ cases: any[] }>(`/api/incidents/${id}/similar-cases`);
export const getDemoScenarios = () => get<{ scenarios: any[]; demo_mode?: boolean }>("/api/demo/scenarios");
/** A fresh demo session (demo backends only): closes the old one, deletes nothing. */
export const resetDemo = (plant: string) =>
  postDetail<{ run_id: string; stopped_run: string | null; closed_incidents: string[] }>(
    "/api/demo/reset", { plant_id: plant });
export const startDemoScenario = (key: string, plant: string, speed = 60) =>
  postDetail<any>(`/api/demo/scenarios/${key}/start`, { plant_id: plant, speed });
export const openIncident = (body: Record<string, unknown>) => postDetail<any>("/api/incidents", body);
export const approveRecommendation = (id: string, optionId: string, note?: string) =>
  postDetail<any>(`/api/recommendations/${id}/approve`, { option_id: optionId, note });
export const rejectRecommendation = (id: string, reason: string) =>
  postDetail<any>(`/api/recommendations/${id}/reject`, { reason });
export const modifyRecommendation = (
  id: string, optionId: string, windowId: string | null, note: string,
  extra: { priority?: "low" | "normal" | "high"; description?: string } = {},
) =>
  postDetail<any>(`/api/recommendations/${id}/modify`,
                  { option_id: optionId, schedule_window_id: windowId, note, ...extra });
export type InvestigationFocus = "more_evidence" | "supplier_lot";
export const requestMoreInvestigation = (id: string, note: string, focus: InvestigationFocus[] = []) =>
  postDetail<any>(`/api/recommendations/${id}/request-investigation`, { note, focus });
export const executeAction = (id: string) => postDetail<any>(`/api/actions/${id}/execute`, {});
export const getAiMetrics = (plant?: string) =>
  get<Record<string, any>>(`/api/ai/operations/metrics${plant ? `?plant_id=${plant}` : ""}`);
export const getBusinessImpact = (plant: string) =>
  get<{ lines: any[]; unit_value_usd: any }>(`/api/business/impact?plant_id=${plant}`);

// ------------------------------------------------------ stakeholder views (ADR-071)
//
// Supervisor: the Vehicle Timeline (server-side filter / search / pagination; one vehicle's
// story fetched lazily on expand). Manager: the Decision Queue -- ONLY recommendations that
// need the caller's authority, decided server-side by the autonomy governor.

export type VehicleStatus = "normal" | "at_risk" | "inspection_required" | "incident" | "completed";
export type EventSource = "SENSOR" | "INFERRED" | "MANUAL" | "AI" | "SYSTEM" | "VERIFIED" | "MANAGER" | "SIMULATION";

export interface Vehicle {
  unit_id: string; variant: string | null; lot_id: string | null;
  current_station: number | null; current_station_name: string | null;
  status: VehicleStatus; risk: number | null; risk_threshold: number;
  /** 0..1: how far along the line the vehicle is. */
  progress: number | null;
  entered_at: string | null; updated_at: string; eta: string | null; eta_basis: string;
  inspected: boolean; defect_found: boolean | null; incidents: string[];
}
export interface InvestigationCard {
  incident_id: string; title: string; kind: string; station_id: number | null; status: string;
  trigger_ts: string; investigation_status: string | null; now: string | null;
  observation: string;
  evidence: { text: string; provenance: string; supports: boolean }[];
  hypotheses: { title: string; confidence: number; status: string }[];
  confidence: number | null; assessment: string | null;
  recommendation: { status: string; root_cause: string | null; action: string | null } | null;
  follow_ups: TimelineEvent[];
}
export interface TimelineEvent {
  ts: string; clock: "simulation" | "wall"; source: EventSource;
  category: "journey" | "process" | "manual" | "quality" | "ai" | "incident";
  text: string; station_id: number | null; flagged?: boolean; station_state?: string;
  investigation?: InvestigationCard;
}
export interface VehicleTimelineData {
  vehicle: Vehicle & { quality_status: string };
  run_id: string; events: TimelineEvent[]; incidents: InvestigationCard[];
}
export const listVehicles = (plant: string, o: { status?: string; q?: string; limit?: number; offset?: number } = {}) => {
  const qs = new URLSearchParams({ plant_id: plant, status: o.status ?? "all",
                                   limit: String(o.limit ?? 25), offset: String(o.offset ?? 0) });
  if (o.q) qs.set("q", o.q);
  return get<{ run: { run_id: string; status: string; simulation_time: string | null } | null; vehicles: Vehicle[]; total: number;
               counts: Record<VehicleStatus, number> }>(`/api/vehicles?${qs}`);
};
export const getVehicleTimeline = (plant: string, unitId: string, runId?: string | null) =>
  get<VehicleTimelineData>(`/api/vehicles/${encodeURIComponent(unitId)}/timeline?plant_id=${plant}`
    + (runId ? `&run_id=${encodeURIComponent(runId)}` : ""));

export interface DecisionCardData {
  recommendation_id: string; incident_id: string; severity: string; title: string;
  problem: string; conclusion: string | null; confidence: number | null;
  affected: { count: number | null; basis: string };
  action: { option_id: string; action_type: string; title: string; adapter: string; station_id: number | null };
  expected_impact: { mean: number; ci_low: number; ci_high: number; metric: string; basis: string;
                     experiment_id: string; improves: boolean } | null;
  risk: string; reversible: boolean; downtime_min: number; downtime_basis: string;
  cost: { value_usd: number; basis: string; sources: string[] } | null;
  schedule: string | null; schedule_windows: { window_id: string; name: string; start: string }[];
  recommended_timing: string | null;
  approval: { required_level: string; granted_level: string; reason: string; details: string[] };
}
export interface DecisionEvidence {
  points: { text: string; supports: boolean; against: string | null }[];
  twin_tests: { label: string; tests: string; metric: string; mean: number | null; ci_low: number | null;
                ci_high: number | null; baseline_uph: number | null; throughput_delta: number | null;
                effect: string; experiment_id: string }[];
  leading: { title: string; confidence: number }[];
  alternatives: { title: string; confidence: number }[];
  conclusion: string | null; assessment: string | null;
  observation: string | null;
  investigated: { specialist: string; what: string }[];
  explanations: { title: string; confidence: number; status: string }[];
  recommendation: string | null;
  approval: { decision: string; reasons: string[] } | null;
}
export interface DecisionHistoryItem {
  recommendation_id: string; incident_id: string; problem: string | null; action: string | null;
  decision: string; decided_by: string | null; decided_at: string | null; note: string | null;
  actions: { action_type: string; status: string; reference: string | null }[];
  outcome: { status: string; reason: string | null; prediction_error: number | null } | null;
}
export const getPendingDecisions = (plant: string) =>
  get<{ decisions: DecisionCardData[]; summary: { pending: number; high_risk: number;
        approved_today: number; in_progress: number } }>(`/api/decisions/pending?plant_id=${plant}`);
export const getDecisionHistory = (plant: string) =>
  get<{ history: DecisionHistoryItem[] }>(`/api/decisions/history?plant_id=${plant}`);
export const getDecisionEvidence = (recId: string) =>
  get<DecisionEvidence>(`/api/decisions/${encodeURIComponent(recId)}/evidence`);
