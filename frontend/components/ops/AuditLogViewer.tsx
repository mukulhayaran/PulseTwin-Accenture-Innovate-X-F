"use client";

// Searchable table over GET /api/audit/log (migrations/002 -- CLAUDE.md's audit-trail
// contract: every mutating action is logged with role/action/resource/payload). Filtering
// re-queries the server rather than filtering a client-side cache, since the server caps
// each response at `limit` rows and a client-side filter over a partial page would
// silently hide matches that never made it into the page.

import { useEffect, useState } from "react";
import { getAuditActions, getAuditLog } from "@/lib/api";

const ROLES = ["", "supervisor", "manager", "leadership", "system"];

export default function AuditLogViewer() {
  const [entries, setEntries] = useState<any[]>([]);
  const [actions, setActions] = useState<string[]>([]);
  const [role, setRoleFilter] = useState("");
  const [action, setAction] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => { getAuditActions().then((r) => setActions(r.actions)).catch(() => setActions([])); }, []);

  useEffect(() => {
    setLoading(true);
    getAuditLog({ role: role || undefined, action: action || undefined, limit: 50 })
      .then((r) => setEntries(r.entries))
      .catch(() => setEntries([]))
      .finally(() => setLoading(false));
  }, [role, action]);

  return (
    <Panel title="AUDIT LOG">
      <div className="flex flex-wrap gap-2 mb-2 text-[11px]">
        <select
          value={role}
          onChange={(e) => setRoleFilter(e.target.value)}
          className="bg-bg-primary border border-bg-surface rounded px-1.5 py-0.5"
        >
          {ROLES.map((r) => <option key={r} value={r}>{r || "all roles"}</option>)}
        </select>
        <select
          value={action}
          onChange={(e) => setAction(e.target.value)}
          className="bg-bg-primary border border-bg-surface rounded px-1.5 py-0.5"
        >
          <option value="">all actions</option>
          {actions.map((a) => <option key={a} value={a}>{a}</option>)}
        </select>
        {loading && <span className="text-txt-muted self-center">loading…</span>}
      </div>

      <div className="max-h-72 overflow-y-auto">
        <table className="w-full text-[11px] border-collapse">
          <thead className="sticky top-0 bg-bg-card">
            <tr className="text-txt-muted text-left">
              <th className="pr-3 pb-1 font-normal">TIME</th>
              <th className="pr-3 pb-1 font-normal">ROLE</th>
              <th className="pr-3 pb-1 font-normal">ACTION</th>
              <th className="pr-3 pb-1 font-normal">RESOURCE</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((e) => (
              <tr key={e.id} className="border-t border-bg-surface align-top">
                <td className="pr-3 py-1 text-txt-muted whitespace-nowrap">
                  {String(e.timestamp).slice(0, 19).replace("T", " ")}
                </td>
                <td className="pr-3 py-1">{e.user_role}</td>
                <td className="pr-3 py-1 text-signal-blue">{e.action}</td>
                <td className="pr-3 py-1 text-txt-muted">
                  {e.resource_type ? `${e.resource_type}:${e.resource_id ?? ""}` : "—"}
                </td>
              </tr>
            ))}
            {entries.length === 0 && !loading && (
              <tr><td colSpan={4} className="py-4 text-center text-txt-muted">No matching entries.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-bg-card border border-bg-surface rounded p-3">
      <h2 className="text-txt-muted tracking-widest text-[11px] mb-2">{title}</h2>
      {children}
    </div>
  );
}
