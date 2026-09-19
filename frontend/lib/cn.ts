import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

// Merges conditional class lists and resolves Tailwind conflicts so a caller's
// override actually wins: cn("p-3", props.className) with className="p-0" gives p-0,
// where a plain template string would emit both and let source order decide.
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
