"use client";

import { useEffect, useState } from "react";
import { type Role, getRole, roleAtLeast, setRole } from "@/lib/api";

// Gates a fixed persona page's ENTRY on whatever role /login (or the "leadership"
// default) left in place, then locks the page to its own fixed persona once entry is
// granted -- two different questions kept deliberately separate. A leadership login on
// /manager must see EXACTLY what a manager sees, not some blended view, which is why
// `setRole(pageRole)` still runs after the check rather than trusting entryRole for
// display (CLAUDE.md SS7: "the four views are not symmetric").
//
// `entryRole` is captured in the lazy useState initializer -- a pure read, so it is safe
// under StrictMode's double-invoke -- specifically so it reads whatever role was set
// BEFORE this page's own mount, not after. Reading it inside the gating effect instead
// would be circular once that same effect calls setRole(pageRole).
//
// Returns null while the check is pending (first render), then true/false.
export function usePersonaAccess(pageRole: Role, minRole: Role = pageRole): boolean | null {
  const [entryRole] = useState<Role>(() => getRole());
  const [granted, setGranted] = useState<boolean | null>(null);

  useEffect(() => {
    const ok = roleAtLeast(entryRole, minRole);
    if (ok) setRole(pageRole);
    setGranted(ok);
  }, [entryRole, minRole, pageRole]);

  return granted;
}
