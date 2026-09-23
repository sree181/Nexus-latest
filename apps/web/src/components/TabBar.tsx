import { Link, type LinkProps } from "@tanstack/react-router";

export interface Tab {
  label: string;
  to: LinkProps["to"];
  params?: LinkProps["params"];
}

const base =
  "rounded-lg px-3.5 py-2 text-[13px] transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";

/** Section tabs as real links: keyboard-navigable, shareable, and the browser
 *  back button works. Rendered as a nav, not a widget pretending to be one. */
export function TabBar({ label, tabs }: { label: string; tabs: Tab[] }) {
  return (
    <nav
      aria-label={label}
      className="flex shrink-0 items-center gap-1 border-b border-line bg-surface px-6"
    >
      {tabs.map((tab) => (
        <Link
          key={`${String(tab.to)}${JSON.stringify(tab.params ?? {})}`}
          to={tab.to}
          params={tab.params}
          className={`${base} my-2 text-slate hover:bg-surface-2 hover:text-ink`}
          activeProps={{
            className: `${base} my-2 bg-accent-soft font-medium text-accent`,
            "aria-current": "page",
          }}
          activeOptions={{ exact: true }}
        >
          {tab.label}
        </Link>
      ))}
    </nav>
  );
}

/** The four governance views over the fleet. */
export function fleetTabs(): Tab[] {
  return [
    { label: "Overview", to: "/fleet/overview" },
    { label: "Hypergraph", to: "/fleet" },
    { label: "Recommendations", to: "/fleet/recommendations" },
    { label: "Structure", to: "/structure" },
  ];
}

/** The views over one run's governed memory. Rewind sits next to Provenance
 *  because they answer the same question from two directions: why a memory is
 *  held, and what was held at a given moment. */
export function runTabs(runId: string): Tab[] {
  const params = { runId };
  return [
    { label: "Working", to: "/runs/$runId", params },
    { label: "Code", to: "/runs/$runId/code", params },
    { label: "Provenance", to: "/runs/$runId/memory", params },
    { label: "Rewind", to: "/runs/$runId/rewind", params },
    { label: "Security", to: "/runs/$runId/security", params },
    { label: "Supply chain", to: "/runs/$runId/supply", params },
  ];
}
