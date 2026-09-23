import type { ReactNode } from "react";
import { Link, type LinkProps } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { api, latestRun } from "../lib/api";
import { useIdentity, type Me } from "../lib/useIdentity";

function Icon({ d }: { d: string }) {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={d} />
    </svg>
  );
}

const row = "flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm";

function NavLink({
  to,
  params,
  exact,
  icon,
  children,
}: {
  to: LinkProps["to"];
  params?: LinkProps["params"];
  exact?: boolean;
  icon: ReactNode;
  children: ReactNode;
}) {
  return (
    <Link
      to={to}
      params={params}
      className={`${row} text-rail-ink-dim transition hover:bg-rail-hover focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-bright`}
      activeProps={{
        className: `${row} bg-rail-2 border-l-[3px] border-accent-bright text-rail-ink`,
        "aria-current": "page",
      }}
      activeOptions={{ exact: exact ?? false }}
    >
      {icon}
      {children}
    </Link>
  );
}

function Unavailable({
  icon,
  children,
  why,
}: {
  icon: ReactNode;
  children: ReactNode;
  why: string;
}) {
  return (
    <span
      aria-disabled="true"
      title={why}
      className={`${row} text-rail-ink-faint`}
    >
      {icon}
      {children}
    </span>
  );
}

const icons = {
  start: "M12 5v14M5 12h14",
  working: "M12 6v6l4 2",
  provenance: "M6 3v12a3 3 0 003 3h9M6 3l-3 3M6 3l3 3",
  security: "M12 3l7 3v5c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z",
  supply: "M12 3l8 4.5v9L12 21l-8-4.5v-9z",
  structure: "M6 6h.01M18 12h.01M6 18h.01",
  audit: "M8 6h9M8 12h9M8 18h5M4 6h.01M4 12h.01M4 18h.01",
  fleet: "M4 5h7v7H4zM13 5h7v7h-7zM4 14h7v6H4zM13 14h7v6h-7z",
  devices: "M4 5h16v10H4zM9 19h6M12 15v4",
  setup: "M8 6l-5 6 5 6M16 6l5 6-5 6",
};

export function Sidebar() {
  // the run links need a real run, and the one worth linking to is the newest:
  // runs come back oldest first, so the seeded one is not it
  const runs = useQuery({ queryKey: ["runs"], queryFn: api.runs });
  const { me, analyst } = useIdentity();
  const runId = latestRun(runs.data);
  const params = runId ? { runId } : undefined;
  const noRun = runs.isPending ? "Loading runs…" : "No run has memory yet";

  return (
    <aside className="flex h-full w-[244px] shrink-0 flex-col overflow-auto bg-rail px-3.5 py-5">
      <div className="flex items-center gap-3 px-2.5 pb-5 pt-1.5">
        <div className="flex h-[30px] w-[30px] items-center justify-center rounded-[9px] bg-accent">
          <svg
            width="17"
            height="17"
            viewBox="0 0 24 24"
            fill="none"
            stroke="#fff"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <circle cx="12" cy="5" r="2" />
            <circle cx="5" cy="19" r="2" />
            <circle cx="19" cy="19" r="2" />
            <path d="M12 7v4M12 11l-6 6M12 11l6 6" />
          </svg>
        </div>
        <span className="font-serif text-[19px] font-semibold text-rail-ink">
          MeshAgent
        </span>
      </div>

      <nav aria-label="Main" className="flex flex-col">
        <p className="mx-3 mb-2 mt-1.5 font-mono text-[11px] tracking-widest text-rail-ink-faint">
          WORKSPACE
        </p>
        <NavLink to="/" exact icon={<Icon d={icons.start} />}>
          Start a task
        </NavLink>

        <p className="mx-3 mb-2 mt-5 font-mono text-[11px] tracking-widest text-rail-ink-faint">
          {runId ? `RUN ${runId}` : "RUN"}
        </p>
        {params ? (
          <>
            <NavLink
              to="/runs/$runId"
              params={params}
              exact
              icon={<Icon d={icons.working} />}
            >
              Working
            </NavLink>
            <NavLink
              to="/runs/$runId/memory"
              params={params}
              icon={<Icon d={icons.provenance} />}
            >
              Provenance
            </NavLink>
            <NavLink
              to="/runs/$runId/security"
              params={params}
              icon={<Icon d={icons.security} />}
            >
              Security
            </NavLink>
            <NavLink
              to="/runs/$runId/supply"
              params={params}
              icon={<Icon d={icons.supply} />}
            >
              Supply chain
            </NavLink>
          </>
        ) : (
          <>
            <Unavailable icon={<Icon d={icons.working} />} why={noRun}>
              Working
            </Unavailable>
            <Unavailable icon={<Icon d={icons.provenance} />} why={noRun}>
              Provenance
            </Unavailable>
            <Unavailable icon={<Icon d={icons.security} />} why={noRun}>
              Security
            </Unavailable>
            <Unavailable icon={<Icon d={icons.supply} />} why={noRun}>
              Supply chain
            </Unavailable>
          </>
        )}

        {/* The fleet reads across every developer's memory, so it is the
            security office's view and nobody else's. The API refuses it
            either way; hiding it here keeps the app from offering something
            it cannot deliver. */}
        {analyst ? (
          <>
            <p className="mx-3 mb-2 mt-5 font-mono text-[11px] tracking-widest text-rail-ink-faint">
              GOVERNANCE
            </p>
            <NavLink to="/fleet/overview" icon={<Icon d={icons.fleet} />}>
              Fleet
            </NavLink>
            <NavLink to="/structure" icon={<Icon d={icons.structure} />}>
              Structure
            </NavLink>
            <NavLink to="/audit" icon={<Icon d={icons.audit} />}>
              Action log
            </NavLink>
          </>
        ) : null}

        {/* Outside the analyst block: registering your own laptop, and
            approving a login waiting on your own screen, is the one
            governance action that belongs to the developer. */}
        <p className="mx-3 mb-2 mt-5 font-mono text-[11px] tracking-widest text-rail-ink-faint">
          THIS MACHINE
        </p>
        <NavLink to="/setup" icon={<Icon d={icons.setup} />}>
          Connect your editor
        </NavLink>
        <NavLink to="/devices" icon={<Icon d={icons.devices} />}>
          Devices
        </NavLink>
      </nav>

      <Identity me={me} />
    </aside>
  );
}

function initials(name: string): string {
  const parts = name.replace(/@.*$/, "").split(/[.\s_-]+/).filter(Boolean);
  const letters = parts.slice(0, 2).map((p) => p[0]).join("");
  return (letters || name.slice(0, 2)).toUpperCase();
}

function Identity({ me }: { me: Me | undefined }) {
  if (!me) return null;
  const label = me.role === "analyst" ? "Security office" : "Developer";
  return (
    <div className="mt-auto border-t border-rail-line p-2.5">
      <div className="flex items-center gap-2.5">
        <div className="flex h-[30px] w-[30px] items-center justify-center rounded-full bg-rail-2 font-mono text-xs text-accent-bright">
          {initials(me.name)}
        </div>
        <div className="flex min-w-0 flex-col">
          <span className="truncate text-[13px] text-rail-ink-dim">{me.name}</span>
          <span className="text-[11px] text-rail-ink-faint">{label}</span>
        </div>
      </div>

      {/* Never let an asserted identity read as a login. */}
      {!me.verified ? (
        <p className="mt-2.5 rounded-md bg-rail-2 px-2.5 py-2 text-[11px] leading-snug text-rail-ink-faint">
          Not signed in. No identity provider is configured, so this is who
          this browser says it is — nothing has verified it.
        </p>
      ) : null}
    </div>
  );
}
