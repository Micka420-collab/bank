// Jeu d'icônes maison : trait 1,75, bouts ronds, 24 px de boîte. Une icône par destination, jamais décorative.

import type { SVGProps } from "react";

const PATHS: Record<string, string> = {
  today: "M4 11.5 12 5l8 6.5M6 10v9h12v-9M10 19v-5h4v5",
  operations: "M4 6h16M4 12h10M4 18h7M17 16l2 2 4-4",
  aids: "M12 3v4M5 7h14l-1.5 9h-11zM9 21h6M8 11h8",
  wealth: "M4 19V9M10 19V5M16 19v-8M22 19H2M19 6l-3 3-3-2-4 4",
  situation: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8ZM4 21a8 8 0 0 1 16 0",
  connectors: "M9 7V3M15 7V3M7 7h10v4a5 5 0 0 1-10 0V7ZM12 16v5",
  settings: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm7.4-3 1.6-1.2-1.5-2.6-1.9.6-1.4-1 .3-2-3-.8-.7 1.9h-1.6L10.5 5l-3 .8.3 2-1.4 1-1.9-.6L3 10.8 4.6 12 3 13.2l1.5 2.6 1.9-.6 1.4 1-.3 2 3 .8.7-1.9h1.6l.7 1.9 3-.8-.3-2 1.4-1 1.9.6 1.5-2.6Z",
  more: "M5 12h.01M12 12h.01M19 12h.01",
  search: "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14Zm9 2-4-4",
  check: "M5 12.5 10 17.5 19 7",
  plus: "M12 5v14M5 12h14",
  arrow: "M5 12h14M13 6l6 6-6 6",
  back: "M19 12H5M11 6l-6 6 6 6",
  bank: "M3 10 12 4l9 6M5 10v9M19 10v9M9 10v9M15 10v9M3 19h18",
  upload: "M12 16V4M6 10l6-6 6 6M4 20h16",
  spark: "M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6",
  shield: "M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6l-8-3Z",
  alert: "M12 9v4M12 17h.01M10.3 4.3 2.8 17a2 2 0 0 0 1.7 3h15a2 2 0 0 0 1.7-3L13.7 4.3a2 2 0 0 0-3.4 0Z",
  calendar: "M4 6h16v14H4zM4 10h16M8 3v4M16 3v4",
  bolt: "M13 2 4 14h7l-1 8 9-12h-7l1-8Z",
  doc: "M7 3h7l5 5v13H7zM14 3v5h5M9 13h6M9 17h6",
  robot: "M5 9h14v10H5zM12 5v4M9 14h.01M15 14h.01M9 2h6",
  cart: "M3 4h2l2.4 11.2a2 2 0 0 0 2 1.6h7.5a2 2 0 0 0 2-1.5L21 8H6M9 21h.01M17 21h.01",
  trendUp: "M4 17 10 11l4 4 6-7M14 8h6v6",
  trendDown: "M4 7l6 6 4-4 6 7M14 16h6v-6",
  close: "M6 6l12 12M18 6 6 18",
  logout: "M10 17l5-5-5-5M15 12H3M12 4h6a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-6",
};

export type IconName = keyof typeof PATHS;

export function Icon({ name, size, ...rest }: { name: IconName; size?: number } & SVGProps<SVGSVGElement>) {
  const d = PATHS[name] ?? PATHS.more!;
  return (
    <svg
      className="icon"
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      <path d={d} />
    </svg>
  );
}
