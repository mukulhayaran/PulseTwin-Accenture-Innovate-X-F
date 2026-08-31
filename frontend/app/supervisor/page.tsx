"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Containment, StationCfg, StreamEvent, UnitDetail, Zone,
  getContainment, getStations, getUnit, postFeedback, streamUrl,
} from "@/lib/api";

const STATE_COLOR: Record<string, string> = {
  working: "bg-signal-green/80 border-signal-green",
  blocked: "bg-signal-red/70 border-signal-red",
  starved: "bg-signal-amber/70 border-signal-amber",
  down: "bg-signal-red border-red-300",
  idle: "bg-bg-surface border-txt-muted/40",
};

interface LiveStation {
  state: string;
  unit_id?: string;
  cycle_time_s?: number;
  risk?: number;
}

export default function SupervisorPage() {
  const [plant, setPlant] = useState("plant_a");
  const [speed, setSpeed] = useState(10);
  const [stations, setStations] = useState<StationCfg[]>([]);
  const [zones, setZones] = useState<Zone[]>([]);
  const [live, setLive] = useState<Record<number, LiveStation>>({});
  const [alerts, setAlerts] = useState<StreamEvent[]>([]);
  const [budget, setBudget] = useState({ used: 0, max: 6 });
  const [clock, setClock] = useState<string>("");
  const [connected, setConnected] = useState(false);
  const [selected, setSelected] = useState<UnitDetail | null>(null);
  const [containment, setContainment] = useState<Containment | null>(null);
  const [voted, setVoted] = useState<Record<string, string>>({});

  // SSE at 50x delivers ~1,400 events/sec. One setState per event locks the browser at
  // exactly the moment the demo shows off speed control, so events are buffered and
  // flushed once per animation frame (docs/AUDIT.md L25).
  const buffer = useRef<StreamEvent[]>([]);
  const frame = useRef<number | null>(null);

  const flush = useCallback(() => {
    frame.current = null;
    const batch = buffer.current;
    if (!batch.length) return;
    buffer.current = [];

    setLive((prev) => {
      const next = { ...prev };
      for (const e of batch) {
        next[e.station_id] = {
          state: e.station_state === "working" ? "working" : e.station_state,
          unit_id: e.unit_id,
          cycle_time_s: e.cycle_time_s,
          risk: e.calibrated_risk,
        };
      }
      return next;
    });

    const last = batch[batch.length - 1];
    setClock(last.ts);
    if (last.alerts_budget) setBudget({ used: last.alerts_used ?? 0, max: last.alerts_budget });

    const newAlerts = batch.filter((e) => e.alert);
    if (newAlerts.length) {
      setAlerts((prev) => [...newAlerts.reverse(), ...prev].slice(0, 40));
    }
  }, []);

  const push = useCallback((e: StreamEvent) => {
    buffer.current.push(e);
    if (frame.current === null) frame.current = requestAnimationFrame(flush);
  }, [flush]);

  useEffect(() => {
    getStations(plant).then((r) => { setStations(r.stations); setZones(r.zones); });
    setLive({}); setAlerts([]); setSelected(null); setContainment(null);
  }, [plant]);

  useEffect(() => {
    let es: EventSource | null = null;
    let cancelled = false;
    streamUrl(plant, speed).then((url) => {
      if (cancelled) return;
      es = new EventSource(url);
      es.onopen = () => setConnected(true);
      es.onerror = () => setConnected(false);
      es.addEventListener("station_update", (ev) => push(JSON.parse((ev as MessageEvent).data)));
      es.addEventListener("alert", (ev) => push(JSON.parse((ev as MessageEvent).data)));
    });
    return () => { cancelled = true; es?.close(); setConnected(false); };
  }, [plant, speed, push]);

  const openUnit = async (unitId: string) => {
    const [u, c] = await Promise.all([getUnit(unitId, plant), getContainment(unitId, plant)]);
    setSelected(u); setContainment(c);
  };

  const vote = async (unitId: string, verdict: string) => {
    setVoted((v) => ({ ...v, [unitId]: verdict }));
    await postFeedback(plant, unitId, verdict);
  };

  const budgetColor =
    budget.used >= budget.max ? "text-signal-red"
      : budget.used >= budget.max - 1 ? "text-signal-amber" : "text-signal-green";

  const simTime = useMemo(() => (clock ? clock.slice(11, 19) : "--:--:--"), [clock]);
  const simDate = useMemo(() => (clock ? clock.slice(0, 10) : ""), [clock]);

  return (
    <div className="min-h-screen bg-bg-primary text-txt-primary font-mono text-[13px]">
      {/* ------------------------------------------------------------- top bar */}
      <header className="flex flex-wrap items-center gap-x-6 gap-y-2 border-b border-bg-surface px-4 py-2">
        <div className="font-semibold tracking-wide text-signal-blue">PULSETWIN</div>

        <select
          value={plant}
          onChange={(e) => setPlant(e.target.value)}
          className="bg-bg-card border border-bg-surface px-2 py-1 rounded"
        >
          <option value="plant_a">Plant A — Midwest Assembly</option>
          <option value="plant_b">Plant B — Southern Truck</option>
        </select>

        <div className="flex items-center gap-1">
          <span className="text-txt-muted">SPEED</span>
          {[1, 5, 10, 50].map((s) => (
            <button
              key={s}
              onClick={() => setSpeed(s)}
              className={`px-2 py-0.5 rounded border ${
                speed === s
                  ? "border-signal-blue text-signal-blue"
                  : "border-bg-surface text-txt-muted hover:text-txt-primary"
              }`}
            >
              {s}×
            </button>
          ))}
        </div>

        {/* Budget is SERVER-derived (ADR-012): it survives a refresh and view switches,
            which client-side accumulation could not, since localStorage is banned. */}
        <div className="flex items-center gap-2">
          <span className="text-txt-muted">ALERTS</span>
          <span className={`font-semibold ${budgetColor}`}>
            {budget.used} / {budget.max}
          </span>
          <div className="h-1.5 w-24 bg-bg-surface rounded overflow-hidden">
            <div
              className={`h-full ${
                budget.used >= budget.max ? "bg-signal-red"
                  : budget.used >= budget.max - 1 ? "bg-signal-amber" : "bg-signal-green"
              }`}
              style={{ width: `${Math.min(100, (budget.used / budget.max) * 100)}%` }}
            />
          </div>
          <span className="text-txt-muted text-[11px]">this shift</span>
        </div>

        <div className="ml-auto flex items-center gap-4">
          <div className="text-txt-muted">
            {simDate} <span className="text-txt-primary">{simTime}</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span
              className={`inline-block h-2 w-2 rounded-full ${
                connected ? "bg-signal-green" : "bg-signal-red animate-pulse"
              }`}
            />
            <span className="text-txt-muted">{connected ? "SSE LIVE" : "RECONNECTING"}</span>
          </div>
        </div>
      </header>

      <div className="grid grid-cols-1 lg:grid-cols-[65fr_35fr] gap-3 p-3">
        {/* ------------------------------------------------------- line map */}
        <section className="space-y-3">
          <div className="bg-bg-card border border-bg-surface rounded p-3">
            <div className="flex items-baseline justify-between mb-3">
              <h2 className="text-txt-muted tracking-widest text-[11px]">LINE MAP</h2>
              <div className="flex gap-3 text-[11px] text-txt-muted">
                <Legend color="bg-signal-green" label="working" />
                <Legend color="bg-signal-red/70" label="blocked" />
                <Legend color="bg-signal-amber/70" label="starved" />
                <span className="text-signal-amber">▨ INFERRED (no sensors)</span>
              </div>
            </div>

            <div className="space-y-4">
              {zones.map((z) => (
                <div key={z.id}>
                  <div className="text-[11px] text-txt-muted tracking-widest mb-1.5">
                    {z.name.toUpperCase()}
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {stations
                      .filter((s) => s.zone === z.id)
                      .map((s) => {
                        const l = live[s.station_id];
                        const manual = s.sensor_tier === "manual";
                        return (
                          <button
                            key={s.station_id}
                            onClick={() => l?.unit_id && openUnit(l.unit_id)}
                            title={`${s.station_name}${manual ? " — MANUAL, no continuous sensors" : ""}`}
                            className={`relative w-[70px] h-[54px] rounded border text-left px-1.5 py-1
                              transition-colors ${STATE_COLOR[l?.state ?? "idle"] ?? STATE_COLOR.idle}`}
                            style={
                              manual
                                ? {
                                    backgroundImage:
                                      "repeating-linear-gradient(45deg, transparent, transparent 4px, rgba(245,158,11,.35) 4px, rgba(245,158,11,.35) 8px)",
                                  }
                                : undefined
                            }
                          >
                            <div className="text-[11px] font-semibold">S{s.station_id}</div>
                            <div className="text-[9px] leading-tight opacity-80 truncate">
                              {s.station_name}
                            </div>
                            {l?.cycle_time_s && (
                              <div className="text-[9px] opacity-90">
                                {l.cycle_time_s.toFixed(0)}s
                              </div>
                            )}
                            {manual && (
                              <span className="absolute top-0.5 right-0.5 text-[7px] text-signal-amber font-bold">
                                INF
                              </span>
                            )}
                          </button>
                        );
                      })}
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* ------------------------------------------------- unit tracker */}
          {selected && (
            <div className="bg-bg-card border border-bg-surface rounded p-3">
              <div className="flex items-center justify-between mb-2">
                <h2 className="text-txt-muted tracking-widest text-[11px]">
                  UNIT {selected.unit_id} · {selected.variant.toUpperCase()} · LOT {selected.lot_id}
                </h2>
                <button onClick={() => setSelected(null)} className="text-txt-muted hover:text-txt-primary">✕</button>
              </div>

              <div className="flex gap-6 mb-3">
                <Stat label="RISK" value={`${(selected.calibrated_risk * 100).toFixed(0)}%`}
                      sub={`CI ${(selected.ci_low * 100).toFixed(0)}–${(selected.ci_high * 100).toFixed(0)}%`}
                      tone={selected.calibrated_risk > 0.5 ? "text-signal-red" : "text-signal-amber"} />
                <Stat label="LEAD TIME" value={`${selected.lead_time_min.toFixed(0)} min`} sub="before final gate" />
                <Stat label="ALERT" value={selected.alert_fired ? "FIRED" : "below budget"}
                      tone={selected.alert_fired ? "text-signal-red" : "text-txt-muted"} />
              </div>

              <div className="text-[11px] text-txt-muted mb-1">ROOT CAUSES</div>
              <div className="space-y-1 mb-3">
                {selected.factors.map((f) => (
                  <FactorBar key={f.rank} f={f} max={Math.max(...selected.factors.map((x) => Math.abs(x.contribution)))} />
                ))}
              </div>

              <div className="text-[11px] text-txt-muted mb-1">JOURNEY</div>
              <div className="flex flex-wrap gap-1">
                {selected.journey.map((j) => (
                  <span key={j.station_id}
                        className="px-1.5 py-0.5 rounded bg-bg-surface text-[10px]">
                    S{j.station_id} <span className="text-txt-muted">{j.cycle_s.toFixed(0)}s</span>
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* -------------------------------------------------- containment */}
          {containment && containment.at_risk_units.length > 0 && (
            <div className="bg-bg-card border border-signal-amber/40 rounded p-3">
              <h2 className="text-signal-amber tracking-widest text-[11px] mb-2">
                CONTAINMENT — {containment.total_at_risk} UNITS SHARE THIS FINGERPRINT
              </h2>
              <table className="w-full text-[11px]">
                <thead className="text-txt-muted">
                  <tr className="text-left">
                    <th className="py-1">UNIT</th><th>AT</th><th>RISK</th>
                    <th>SHARED</th><th>ACTION</th>
                  </tr>
                </thead>
                <tbody>
                  {containment.at_risk_units.map((u) => (
                    <tr key={u.unit_id} className="border-t border-bg-surface">
                      <td className="py-1">{u.unit_id}</td>
                      <td>S{u.current_station}</td>
                      <td className={u.risk > 0.5 ? "text-signal-red" : "text-signal-amber"}>
                        {(u.risk * 100).toFixed(0)}%
                      </td>
                      <td className="text-txt-muted">{u.shared_factors.join(", ")}</td>
                      <td className={
                        u.action === "HOLD" ? "text-signal-red"
                          : u.action === "INSPECT" ? "text-signal-amber" : "text-txt-muted"
                      }>{u.action}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="text-[10px] text-txt-muted mt-2">
                Containment window {containment.containment_window_min} min · operator
                identity shown as group only
              </div>
            </div>
          )}
        </section>

        {/* --------------------------------------------------------- alert feed */}
        <section className="bg-bg-card border border-bg-surface rounded p-3">
          <h2 className="text-txt-muted tracking-widest text-[11px] mb-2">ALERT FEED</h2>
          {alerts.length === 0 && (
            <div className="text-txt-muted text-[11px] py-8 text-center">
              No alerts yet — the line is inside the alarm budget.
            </div>
          )}
          <div className="space-y-2 max-h-[calc(100vh-8rem)] overflow-y-auto pr-1">
            {alerts.map((a, i) => (
              <div key={`${a.unit_id}-${i}`}
                   className="border border-signal-red/40 rounded p-2 bg-signal-red/5">
                <div className="flex items-baseline justify-between">
                  <span className="text-signal-red font-semibold text-[15px]">
                    {(a.calibrated_risk * 100).toFixed(0)}%
                  </span>
                  <span className="text-txt-muted text-[10px]">{a.ts.slice(11, 19)}</span>
                </div>
                <div className="text-[11px]">
                  {a.unit_id} · {a.variant.toUpperCase()} · Lot {a.lot_id}
                </div>
                <div className="text-[10px] text-txt-muted">
                  at S{a.station_id} · cycle {a.cycle_time_s.toFixed(0)}s
                </div>
                <div className="flex gap-1.5 mt-1.5">
                  <button onClick={() => openUnit(a.unit_id)}
                          className="px-2 py-0.5 border border-signal-blue text-signal-blue rounded text-[10px]">
                    Show units ↗
                  </button>
                  <button onClick={() => vote(a.unit_id, "useful")}
                          className={`px-2 py-0.5 border rounded text-[10px] ${
                            voted[a.unit_id] === "useful"
                              ? "border-signal-green text-signal-green"
                              : "border-bg-surface text-txt-muted"}`}>
                    👍
                  </button>
                  <button onClick={() => vote(a.unit_id, "false_alarm")}
                          className={`px-2 py-0.5 border rounded text-[10px] ${
                            voted[a.unit_id] === "false_alarm"
                              ? "border-signal-red text-signal-red"
                              : "border-bg-surface text-txt-muted"}`}>
                    👎
                  </button>
                </div>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

function Legend({ color, label }: { color: string; label: string }) {
  return (
    <span className="flex items-center gap-1">
      <span className={`inline-block h-2 w-2 rounded-sm ${color}`} />
      {label}
    </span>
  );
}

function Stat({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: string }) {
  return (
    <div>
      <div className="text-[10px] text-txt-muted tracking-widest">{label}</div>
      <div className={`text-lg font-semibold ${tone ?? ""}`}>{value}</div>
      {sub && <div className="text-[10px] text-txt-muted">{sub}</div>}
    </div>
  );
}

function FactorBar({ f, max }: { f: { plain_language: string; contribution: number; direction: string }; max: number }) {
  const pct = Math.min(100, (Math.abs(f.contribution) / (max || 1)) * 100);
  return (
    <div>
      <div className="flex justify-between text-[11px]">
        <span>{f.plain_language}</span>
        <span className={f.direction === "high" ? "text-signal-red" : "text-signal-blue"}>
          {f.direction === "high" ? "▲" : "▼"} {f.contribution.toFixed(3)}
        </span>
      </div>
      <div className="h-1 bg-bg-surface rounded overflow-hidden">
        <div className={`h-full ${f.direction === "high" ? "bg-signal-red" : "bg-signal-blue"}`}
             style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}
