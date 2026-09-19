"use client";

// Searchable table over GET /api/audit/log (migrations/002 -- CLAUDE.md's audit-trail
// contract: every mutating action is logged with role/action/resource/payload). Filtering
// re-queries the server rather than filtering a client-side cache, since the server caps
// each response at `limit` rows and a client-side filter over a partial page would
// silently hide matches that never made it into the page.

import { useEffect, useState } from "react";
import { getAuditActions, getAuditLog } from "@/lib/api";
import { Badge, DataTable, Panel, Select, SkeletonTable, type Column } from "@/components/ui";

const ROLES = ["", "supervisor", "manager", "leadership", "system"];

const ROLE_VARIANT: Record<string, "ok" | "info" | "ai" | "warn" | "neutral"> = {
  supervisor: "ok",
  manager: "info",
  leadership: "ai",
  system: "warn",
};

export default function AuditLogViewer() {
  const [entries, setEntries] = useState<any[]>([]);
  const [actions, setActions] = useState<string[]>([]);
  const [role, setRoleFilter] = useState("");
  const [action, setAction] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => { getAuditActions().then((r) => setActions(r.actions)).catch(() => setActions([])); }, []);

  useEffect(() => {
    setLoading(true);
    // role/action are passed as undefined when empty -- NOT as "": an empty string still
    // serialises into the query and the backend matched it literally (QA bug #34).
    getAuditLog({ role: role || undefined, action: action || undefined, limit: 50 })
      .then((r) => setEntries(r.entries))
      .catch(() => setEntries([]))
      .finally(() => setLoading(false));
  }, [role, action]);

  const columns: Column<any>[] = [
    {
      key: "time", header: "Time", width: "9.5rem",
      cell: (e) => (
        <span className="text-numeric whitespace-nowrap text-2xs text-ink-faint">
          {String(e.timestamp).slice(0, 19).replace("T", " ")}
        </span>
      ),
    },
    {
      key: "role", header: "Role", width: "6.5rem",
      cell: (e) => <Badge variant={ROLE_VARIANT[e.user_role] ?? "neutral"}>{e.user_role}</Badge>,
    },
    {
      key: "action", header: "Action",
      cell: (e) => <span className="text-numeric text-info-fg">{e.action}</span>,
    },
    {
      key: "resource", header: "Resource",
      cell: (e) => (
        <span className="text-numeric text-ink-faint">
          {e.resource_type ? `${e.resource_type}:${e.resource_id ?? ""}` : "—"}
        </span>
      ),
    },
  ];

  return (
    <Panel
      title="Audit log"
      note="Every mutating action — IATF 16949 traceability"
      actions={
        <div className="flex flex-wrap items-center gap-1.5">
          <Select
            ariaLabel="Filter by role"
            value={role}
            onChange={setRoleFilter}
            options={ROLES.map((r) => ({ value: r, label: r || "All roles" }))}
          />
          <Select
            ariaLabel="Filter by action"
            value={action}
            onChange={setAction}
            options={[
              { value: "", label: "All actions" },
              ...actions.map((a) => ({ value: a, label: a })),
            ]}
          />
        </div>
      }
    >
      {loading && entries.length === 0 ? (
        <SkeletonTable rows={6} cols={4} />
      ) : (
        <DataTable
          columns={columns}
          rows={entries}
          rowKey={(e) => String(e.id)}
          maxHeight={288}
          empty="No matching entries."
        />
      )}
    </Panel>
  );
}
