import { Outlet } from "@tanstack/react-router";

import { DeveloperTopbar } from "../components/DeveloperProject";
import { IconTabs } from "../components/DeveloperVisual";

export const connectionTabs = [
  { label: "Overview", icon: "connect" as const, to: "/developer/connections" as const, exact: true },
  { label: "Editors", icon: "cursor" as const, to: "/developer/connections/editors" as const },
  { label: "Repositories", icon: "repository" as const, to: "/developer/connections/repositories" as const },
  { label: "Devices", icon: "device" as const, to: "/developer/connections/devices" as const },
];

export function DeveloperConnectionsLayout() {
  return (
    <main className="dev-page">
      <DeveloperTopbar title="Connections" />
      <IconTabs label="Connections" tabs={connectionTabs} />
      <Outlet />
    </main>
  );
}
