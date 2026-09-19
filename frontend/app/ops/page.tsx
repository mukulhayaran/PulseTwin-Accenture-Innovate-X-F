"use client";

import { useEffect, useRef, useState } from "react";
import {
  StationCfg,
  getAuditLog, getHealth, getIngestionLag, getModelHealth, getModelRegistry,
  getRole, getStations, getSystemHealth, setRole, type Role,
} from "@/lib/api";
import StaleDataIndicator from "@/components/shared/StaleDataIndicator";
import RoleGate from "@/components/shared/RoleGate";
import { useAuditToast } from "@/components/shared/AuditToast";
import ModelHealthPanel from "@/components/ops/ModelHealthPanel";
import DriftMonitor from "@/components/ops/DriftMonitor";
import SensorHealthGrid from "@/components/ops/SensorHealthGrid";
import IngestionLagChart from "@/components/ops/IngestionLagChart";
import SystemStatusBar from "@/components/ops/SystemStatusBar";
import AuditLogViewer from "@/components/ops/AuditLogViewer";
import RetrainingConsole from "@/components/ops/RetrainingConsole";
import { AppShell, Select, Tooltip } from "@/components/ui";

const ROLES: Role[] = ["supervisor", "manager", "leadership", "system"];
const POLL_MS = 10_000;

export default function OpsPage() {
  const [plant, setPlant] = useState("plant_a");
  // Ops is the one view built to demonstrate the RBAC hierarchy in place, rather than a
  // single fixed persona (CLAUDE.md's finals-brief RoleGate intent) -- the selector below
  // lets a demo drop from "system" down to "manager" and watch panels gate themselves off
  // live, exactly the story `docs/FINALS_DEMO_SCRIPT.md` Act 4 wants to tell.
  const [role, setRoleState] = useState<Role>(() => (getRole() === "supervisor" ? "leadership" : getRole()));

  const [system, setSystem] = useState<any>(null);
  const [lag, setLag] = useState<any>(null);
  const [health, setHealth] = useState<any>(null);
  const [registry, setRegistry] = useState<any>(null);
  const [stations, setStations] = useState<StationCfg[]>([]);
  const [connected, setConnected] = useState(true);
  const [lastKnownAt, setLastKnownAt] = useState<string | null>(null);
  const { show, ToastElement } = useAuditToast();
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => { setRole(role); }, [role]);
  useEffect(() => { getStations(plant).then((r) => setStations(r.stations)); }, [plant]);

  const poll = async () => {
    const atLeastManager = ROLES.indexOf(role) >= ROLES.indexOf("manager");
    const atLeastLeadership = ROLES.indexOf(role) >= ROLES.indexOf("leadership");

    // /api/health has no role requirement -- it is the one call every role can always
    // make, so it alone decides "is the twin reachable at all" for the offline banner.
    // The role-gated calls below decide what THIS role can see, not whether it's online.
    try {
      await getHealth();
      setConnected(true);
      setLastKnownAt(new Date().toISOString());
    } catch {
      setConnected(false);
      return;
    }

    if (atLeastManager) {
      getSystemHealth(plant).then(setSystem).catch(() => setSystem(null));
      getIngestionLag(plant).then(setLag).catch(() => setLag(null));
    } else { setSystem(null); setLag(null); }
    if (atLeastLeadership) {
      getModelHealth(plant).then(setHealth).catch(() => setHealth(null));
      getModelRegistry(plant).then(setRegistry).catch(() => setRegistry(null));
    } else { setHealth(null); setRegistry(null); }
  };

  useEffect(() => {
    poll();
    pollRef.current = setInterval(poll, POLL_MS);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [plant, role]);

  const championMetrics = registry?.history?.find((h: any) => h.status === "champion")?.metrics;
  const hasChallenger = !!registry?.history?.some((h: any) => h.status === "challenger");

  return (
    <AppShell
      persona="System operations"
      plant={plant}
      onPlantChange={setPlant}
      role={role}
      connected={connected}
      lastKnownAt={lastKnownAt}
      onRetry={poll}
      controls={
        <Tooltip content="Drops the client's declared role. Panels below gate themselves off and the API returns 403 for anything above this level — the gate is server-side, this only mirrors it.">
          <label className="flex shrink-0 cursor-help items-center gap-2 rounded-lg border border-warn/35 bg-warn/[0.06] px-2.5 py-1">
            <span className="text-2xs font-medium uppercase tracking-[0.14em] text-warn-fg">
              Viewing as
            </span>
            <Select
              ariaLabel="Viewing as role"
              value={role}
              onChange={(v) => setRoleState(v as Role)}
              options={ROLES.map((r) => ({ value: r, label: r }))}
              className="border-warn/30 bg-transparent text-warn-fg"
            />
          </label>
        </Tooltip>
      }
      status={<StaleDataIndicator timestamp={lastKnownAt} />}
    >
      <div className="space-y-4">
        <SystemStatusBar system={system} />

        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          <RoleGate allowed={["leadership", "system"]}>
            <ModelHealthPanel
              health={health}
              championMetrics={championMetrics}
              hasChallenger={hasChallenger}
            />
          </RoleGate>
          <RoleGate allowed={["leadership", "system"]}>
            <DriftMonitor
              psiSummary={health?.drift?.psi_summary}
              calibration={health?.drift?.calibration}
              sensorCoverage={health?.sensor_coverage}
            />
          </RoleGate>
          <RoleGate allowed={["manager", "leadership", "system"]}>
            <SensorHealthGrid
              stations={stations}
              dropouts={system?.ingestion?.dropout_details ?? []}
            />
          </RoleGate>
          <RoleGate allowed={["manager", "leadership", "system"]}>
            <IngestionLagChart key={plant} lag={lag} />
          </RoleGate>
          <RoleGate allowed={["leadership", "system"]}>
            <AuditLogViewer />
          </RoleGate>
          <RoleGate allowed={["system"]}>
            <RetrainingConsole plant={plant} onAction={show} />
          </RoleGate>
        </div>
      </div>

      {ToastElement}
    </AppShell>
  );
}
