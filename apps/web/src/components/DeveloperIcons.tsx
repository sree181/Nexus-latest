import type { SVGProps } from "react";

export type DevIconName =
  | "activity"
  | "arrow"
  | "branch"
  | "check"
  | "chevron"
  | "code"
  | "connect"
  | "copy"
  | "cursor"
  | "device"
  | "dots"
  | "evidence"
  | "external"
  | "eye"
  | "file"
  | "filter"
  | "folder"
  | "info"
  | "live"
  | "lock"
  | "package"
  | "prompt"
  | "repository"
  | "retry"
  | "search"
  | "security"
  | "session"
  | "terminal"
  | "tool"
  | "user"
  | "warning"
  | "x";

const paths: Record<DevIconName, string> = {
  activity: "M3 12h4l2.5-6 4.5 12 2.5-6H21",
  arrow: "M5 12h14M13 6l6 6-6 6",
  branch: "M6 3v12a4 4 0 004 4h8M6 3l3 3M6 3L3 6M15 5l3 3-3 3M18 8h-6a2 2 0 00-2 2v1",
  check: "M5 12l4 4L19 6",
  chevron: "M8 10l4 4 4-4",
  code: "M8 7l-5 5 5 5M16 7l5 5-5 5M14 4l-4 16",
  connect: "M8 12h8M9 7H7a4 4 0 000 8h2M15 7h2a4 4 0 010 8h-2",
  copy: "M8 8h11v11H8zM5 16H4V5h11v1",
  cursor: "M5 3l14 9-7 2-3 7z",
  device: "M4 5h16v11H4zM9 20h6M12 16v4",
  dots: "M5 12h.01M12 12h.01M19 12h.01",
  evidence: "M5 4h14v16H5zM8 9h8M8 13h5M8 17h7M16 4v4h3",
  external: "M14 4h6v6M20 4l-9 9M19 13v6H5V5h6",
  eye: "M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12zM12 9a3 3 0 110 6 3 3 0 010-6z",
  file: "M6 3h8l4 4v14H6zM14 3v5h5",
  filter: "M4 6h16M7 12h10M10 18h4",
  folder: "M3 6h7l2 2h9v11H3z",
  info: "M12 8h.01M11 12h1v5M12 22a10 10 0 100-20 10 10 0 000 20z",
  live: "M8 8a6 6 0 000 8M5 5a10 10 0 000 14M12 12h.01",
  lock: "M6 10h12v10H6zM8 10V7a4 4 0 018 0v3",
  package: "M12 3l8 4.5v9L12 21l-8-4.5v-9zM4 7.5l8 4.5 8-4.5M12 12v9",
  prompt: "M4 5h16v12H8l-4 4zM8 9h8M8 13h5",
  repository: "M5 4h14v16H5zM9 4v16M12 8h4M12 12h4",
  retry: "M20 7v5h-5M4 17v-5h5M18 12a6 6 0 00-10-4L4 12M6 12a6 6 0 0010 4l4-4",
  search: "M11 19a8 8 0 100-16 8 8 0 000 16zM17 17l4 4",
  security: "M12 3l7 3v5c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z",
  session: "M4 5h16v5H4zM4 14h16v5H4zM7 7.5h.01M7 16.5h.01",
  terminal: "M5 7l5 5-5 5M12 17h7",
  tool: "M14 6a4 4 0 01-5 5L4 16l4 4 5-5a4 4 0 005-5l-3 1-2-2 1-3z",
  user: "M12 12a4 4 0 100-8 4 4 0 000 8zM4 21a8 8 0 0116 0",
  warning: "M12 3l10 18H2zM12 9v5M12 18h.01",
  x: "M6 6l12 12M18 6L6 18",
};

export function DevIcon({
  name,
  size = 20,
  strokeWidth = 1.75,
  ...props
}: SVGProps<SVGSVGElement> & { name: DevIconName; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      <path d={paths[name]} />
    </svg>
  );
}
