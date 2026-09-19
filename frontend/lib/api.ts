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
  ts: string;
  unit_id: string;
  station_id: number;
  variant: string;
  lot_id: string;
  cycle_time_s: number;
  station_state: string;
  sensor_tier: "full" | "manual";
  calibrated_risk: number;
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
}

export interface UnitFactor {
  rank: number; feature: string; direction: string;
  contribution: number; value: number | null; plain_language: string;
}

export interface UnitDetail {
  unit_id: string; variant: string; lot_id: string; shift_id: string;
  calibrated_risk: number; ci_low: number; ci_high: number;
  alert_fired: number; above_cost_threshold: number;
  actual_defect: number; defect_mode: string; lead_time_min: number;
  factors: UnitFactor[];
  journey: { station_id: number; entry_ts: string; exit_ts: string; cycle_s: number; station_state: string }[];
  readings: { station_id: number; signal: string; value: number; provenance: string; ci_low: number | null; ci_high: number | null }[];
}

export interface Containment {
  flagged_unit: string; flagged_risk: number; flagged_factors: string[];
  at_risk_units: { unit_id: string; current_station: number | null; risk: number; shared_factors: string[]; action: string }[];
  containment_window_min: number; total_at_risk: number;
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

// ------------------------------------------------------------------------------ role
//
// Finals brief Phase F4: a lightweight RBAC header, not full OAuth (docs/SECURITY.md has
// the OIDC upgrade path). Kept in-memory only -- CLAUDE.md's "no localStorage/
// sessionStorage in React components" rule extends here, so the role resets to its
// per-view default on reload rather than persisting client-side.
//
// Default is "leadership": every existing view's calls (built before this header
// existed) must keep working without each one explicitly setting a role, and leadership
// sits at the top of the read hierarchy (supervisor < manager < leadership < system) --
// see serve/middleware/auth.py. The navbar role selector (RoleGate.tsx /
// SystemStatusBar.tsx) lets a demo deliberately DROP to "supervisor" to show the 403 a
// real floor-level credential would get on a leadership-only endpoint.
export type Role = "supervisor" | "manager" | "leadership" | "system";
let currentRole: Role = "leadership";
export function setRole(role: Role): void { currentRole = role; }
export function getRole(): Role { return currentRole; }

function authHeaders(): Record<string, string> {
  return { "X-PulseTwin-Role": currentRole };
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
export const getPlants = () => get<any[]>("/api/config/plants");
export const getHealth = () => get<any>("/api/health");
export const getUnit = (id: string, plant: string) =>
  get<UnitDetail>(`/api/units/${id}?plant_id=${plant}`);
export const getContainment = (id: string, plant: string) =>
  get<Containment>(`/api/trace/containment/${id}?plant_id=${plant}`);
export const getBottleneck = (plant: string) =>
  get<Bottleneck>(`/api/bottleneck/current?plant_id=${plant}`);
export const getMetrics = (plant: string) => get<Metrics>(`/api/metrics?plant_id=${plant}`);
export const getStationStats = (plant: string) => get<any[]>(`/api/stations/stats?plant_id=${plant}`);
export const getRoi = (plant: string, scenario = "base") =>
  get<any>(`/api/economics/roi?plant_id=${plant}&scenario=${scenario}`);

export async function postFeedback(plant: string, unitId: string, verdict: string) {
  const base = await apiBase();
  await fetch(`${base}/api/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ plant_id: plant, unit_id: unitId, verdict }),
  });
}

export async function streamUrl(plant: string, speed: number): Promise<string> {
  const base = await apiBase();
  return `${base}/api/stream?plant_id=${plant}&speed=${speed}`;
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

export const getModelHealth = (plant: string) => get<any>(`/api/model/health?plant_id=${plant}`);
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

export const acknowledgeAlert = (unitId: string, plant: string) =>
  post<{ acknowledged: boolean; unit_id: string; by_role: string; acknowledged_at: string }>(
    `/api/alerts/${unitId}/acknowledge`, { plant_id: plant }
  );

// Champion/challenger/retired history for a plant+model type -- backs RetrainingConsole.tsx.
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

export const triggerRetrain = (plant: string) =>
  post<{ job_id: string; status: string }>("/api/admin/trigger-retrain", { plant_id: plant });
export const getRetrainStatus = (jobId: string) =>
  get<any>(`/api/admin/retrain-status/${jobId}`);
export const promoteModel = (modelId: string) =>
  post<any>("/api/admin/promote-model", { model_id: modelId });
export const rollbackModel = (plant: string, modelType = "defect_risk") =>
  post<any>("/api/admin/rollback-model", { plant_id: plant, model_type: modelType });
