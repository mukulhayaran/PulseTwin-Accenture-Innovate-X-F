import { clsx, type ClassValue } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

// `text-numeric` is a custom @utility (app/globals.css), but tailwind-merge sees the
// `text-` prefix and files it under text-colour -- so `cn("text-numeric text-ink-dim")`
// silently DROPPED it. 71 call sites across 19 files were affected, which meant figures
// rendered in Inter instead of JetBrains Mono in some places and not others: worse than
// either, and a quiet violation of CLAUDE.md §7's "monospace for all numbers".
//
// Registering it as its own class group keeps it out of the text-size and text-colour
// groups while leaving their conflict resolution intact. Any future custom utility whose
// name starts with a real Tailwind prefix needs the same treatment.
// The generic registers "numeric" as an ADDITIONAL class-group id; without it the
// config only accepts tailwind-merge's own built-in group names.
const twMerge = extendTailwindMerge<"numeric">({
  extend: { classGroups: { numeric: ["text-numeric"] } },
});

// Merges conditional class lists and resolves Tailwind conflicts so a caller's
// override actually wins: cn("p-3", props.className) with className="p-0" gives p-0,
// where a plain template string would emit both and let source order decide.
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
