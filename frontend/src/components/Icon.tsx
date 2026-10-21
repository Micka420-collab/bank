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
  memory: "M6 3h11a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6zM6 3v18M10 8h5M10 12h5",
  thumbUp: "M7 10v11H4V10zM7 10l4-7a2 2 0 0 1 3 2l-1 5h6a2 2 0 0 1 2 2.3l-1.4 7a2 2 0 0 1-2 1.7H7",
  thumbDown: "M7 14V3H4v11zM7 14l4 7a2 2 0 0 0 3-2l-1-5h6a2 2 0 0 0 2-2.3l-1.4-7A2 2 0 0 0 17.6 3H7",
  undo: "M9 14 4 9l5-5M4 9h10.5a5.5 5.5 0 0 1 0 11H11",
  pin: "M12 17v5M9 3h6l-1 6 3 3v2H7v-2l3-3z",
  trash: "M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3",
  edit: "M4 20h4L19 9l-4-4L4 16zM14 6l4 4",
  newChat: "M12 8v6M9 11h6M4 5h16v11H9l-5 4z",
  camera: "M4 8h3l2-3h6l2 3h3v11H4zM12 17a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z",
  lock: "M6 11h12v10H6zM8.5 11V7.5a3.5 3.5 0 0 1 7 0V11",
  download: "M12 4v11M7 10l5 5 5-5M4 20h16",
  vault: "M4 5h16v14H4zM14 12a2 2 0 1 0-4 0 2 2 0 0 0 4 0ZM12 10V8M7 19v2M17 19v2",
  logout: "M10 17l5-5-5-5M15 12H3M12 4h6a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-6",
  target: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18ZM12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8ZM12 12h.01",
  route: "M6 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4ZM18 9a2 2 0 1 0 0-4 2 2 0 0 0 0 4ZM8 17h7a3 3 0 0 0 0-6H9a3 3 0 0 1 0-6h7",
  news: "M4 5h13v14H6a2 2 0 0 1-2-2zM17 9h3v8a2 2 0 0 1-2 2M7 9h7M7 13h7M7 17h4",
  send: "M21 3 3 10.5l7 2.5 2.5 7zM10 13 21 3",
  external: "M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5",
  clock: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18ZM12 7v5l3 2",
  copy: "M8 8h11v13H8zM5 16H4V3h11v1",
  flag: "M5 21V4M5 4h11l-2 4 2 4H5",
  coin: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18ZM15.5 9a4 4 0 1 0 0 6M7.5 11h6M7.5 13.5h6",
  folder: "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM3 10h18",
  chat: "M5 5h14a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H10l-5 4v-4H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1ZM8.5 10.5h.01M12 10.5h.01M15.5 10.5h.01",
  bell: "M6 16v-5a6 6 0 0 1 12 0v5l1.5 2h-15ZM10 21a2 2 0 0 0 4 0",
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
