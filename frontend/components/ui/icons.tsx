import type { SVGProps } from "react";

// A small stroke-icon set, inline rather than from a package.
//
// The app needed roughly a dozen glyphs for the sidebar rail, the top bar and the
// KPI row (ADR-047). Pulling in lucide-react for that is ~1.3 MB of source and a
// tree-shaking config to get back to the same twelve paths, on a frontend whose
// whole point is that it ships as a static export onto a plant network.
//
// All of them are 24x24 viewBox, 1.6 stroke, currentColor, and size themselves from
// the `size` prop so a caller can write <Gauge size={14} /> next to 11px type.

type IconProps = Omit<SVGProps<SVGSVGElement>, "size"> & { size?: number };

function Icon({ size = 16, children, ...rest }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      focusable="false"
      {...rest}
    >
      {children}
    </svg>
  );
}

export const IconHome = (p: IconProps) => (
  <Icon {...p}>
    <path d="M3 10.5 12 3l9 7.5" />
    <path d="M5.5 9.5V20h13V9.5" />
    <path d="M9.5 20v-6h5v6" />
  </Icon>
);

export const IconUser = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="12" cy="8" r="3.5" />
    <path d="M4.5 20a7.5 7.5 0 0 1 15 0" />
  </Icon>
);

export const IconChart = (p: IconProps) => (
  <Icon {...p}>
    <path d="M4 20V4" />
    <path d="M4 20h16" />
    <path d="M8 16v-5" />
    <path d="M13 16V7" />
    <path d="M18 16v-8" />
  </Icon>
);

export const IconTarget = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="12" cy="12" r="8.5" />
    <circle cx="12" cy="12" r="4.5" />
    <circle cx="12" cy="12" r="1" fill="currentColor" stroke="none" />
  </Icon>
);

export const IconGear = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="12" cy="12" r="3.2" />
    <path d="M12 2.8v2.4M12 18.8v2.4M4.5 12H2.1M21.9 12h-2.4M6.7 6.7 5 5M19 19l-1.7-1.7M17.3 6.7 19 5M5 19l1.7-1.7" />
  </Icon>
);

export const IconSparkle = (p: IconProps) => (
  <Icon {...p}>
    <path d="M12 3.5 13.7 9l5.5 1.7-5.5 1.7L12 18l-1.7-5.6L4.8 10.7 10.3 9z" />
    <path d="M18.5 3v3M20 4.5h-3" />
  </Icon>
);

export const IconBell = (p: IconProps) => (
  <Icon {...p}>
    <path d="M6.5 9.5a5.5 5.5 0 0 1 11 0c0 4 1.5 5.5 1.5 5.5H5s1.5-1.5 1.5-5.5Z" />
    <path d="M10 18.5a2.2 2.2 0 0 0 4 0" />
  </Icon>
);

export const IconSearch = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="10.5" cy="10.5" r="6.5" />
    <path d="m15.5 15.5 4.5 4.5" />
  </Icon>
);

export const IconChevronDown = (p: IconProps) => (
  <Icon {...p}>
    <path d="m6 9.5 6 6 6-6" />
  </Icon>
);

export const IconChevronRight = (p: IconProps) => (
  <Icon {...p}>
    <path d="m9.5 6 6 6-6 6" />
  </Icon>
);

export const IconClock = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M12 7.5V12l3 1.8" />
  </Icon>
);

export const IconAlert = (p: IconProps) => (
  <Icon {...p}>
    <path d="M12 3.8 21 19.5H3z" />
    <path d="M12 9.8v4.4" />
    <circle cx="12" cy="16.9" r="0.9" fill="currentColor" stroke="none" />
  </Icon>
);

export const IconShield = (p: IconProps) => (
  <Icon {...p}>
    <path d="M12 3 19 5.6v5.6c0 4.3-2.9 7.9-7 9.2-4.1-1.3-7-4.9-7-9.2V5.6z" />
  </Icon>
);

export const IconGauge = (p: IconProps) => (
  <Icon {...p}>
    <path d="M4 17a8 8 0 1 1 16 0" />
    <path d="m12 17 4-5" />
  </Icon>
);

export const IconFactory = (p: IconProps) => (
  <Icon {...p}>
    <path d="M3 20V11l5.5 3.5V11L14 14.5V8l7 4v8z" />
    <path d="M3 20h18" />
  </Icon>
);

export const IconGrid = (p: IconProps) => (
  <Icon {...p}>
    <rect x="3.5" y="3.5" width="7" height="7" />
    <rect x="13.5" y="3.5" width="7" height="7" />
    <rect x="3.5" y="13.5" width="7" height="7" />
    <rect x="13.5" y="13.5" width="7" height="7" />
  </Icon>
);

export const IconArrowRight = (p: IconProps) => (
  <Icon {...p}>
    <path d="M4 12h16" />
    <path d="m14 6 6 6-6 6" />
  </Icon>
);

export const IconLogout = (p: IconProps) => (
  <Icon {...p}>
    <path d="M14 4.5H5.5v15H14" />
    <path d="M18.5 12H10" />
    <path d="m15 8.5 3.5 3.5-3.5 3.5" />
  </Icon>
);

// The closed padlock on a route the session may not open (AppShell's rail) and on the
// signed-out user chip. Matches RoleGate/AccessRestricted's inline lock so the three
// read as one signal at three sizes.
export const IconLock = (p: IconProps) => (
  <Icon {...p}>
    <rect x="4.5" y="10.5" width="15" height="10" rx="1.5" />
    <path d="M8 10.5V7.5a4 4 0 0 1 8 0v3" />
  </Icon>
);
