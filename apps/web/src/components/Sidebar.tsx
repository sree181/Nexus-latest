import type { ReactNode } from "react";
import { Link, type LinkProps } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";

import { api } from "../lib/api";
import { useIdentity, type Me } from "../lib/useIdentity";
import {
  oidcEnabled,
  setLocalIdentity,
  signOut,
  type LocalRole,
} from "../lib/auth";

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
        className: `${row} border-l-[3px] border-accent-bright bg-rail-2 text-rail-ink`,
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

function Group({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <p className="mx-3 mb-2 mt-5 font-mono text-[10.5px] tracking-widest text-rail-ink-faint">
        {label}
      </p>
      {children}
    </>
  );
}

const icons = {
  start: "M12 5v14M5 12h14",
  working: "M12 6v6l4 2",
  provenance: "M6 3v12a3 3 0 003 3h9M6 3l-3 3M6 3l3 3",
  security: "M12 3l7 3v5c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z",
  supply: "M12 3l8 4.5v9L12 21l-8-4.5v-9z",
  graph: "M6 6h.01M18 12h.01M6 18h.01M6 6l12 6M18 12L6 18",
  audit: "M8 6h9M8 12h9M8 18h5M4 6h.01M4 12h.01M4 18h.01",
  fleet: "M4 5h7v7H4zM13 5h7v7h-7zM4 14h7v6H4zM13 14h7v6h-7z",
  devices: "M4 5h16v10H4zM9 19h6M12 15v4",
  setup: "M8 6l-5 6 5 6M16 6l5 6-5 6",
  sessions: "M4 5h16v5H4zM4 14h16v5H4zM7 7.5h.01M7 16.5h.01",
  activity: "M3 12h4l2-6 4 12 2-6h6",
  queue: "M5 7h14M5 12h14M5 17h9",
  cases: "M5 5h14v15H5zM9 5V3h6v2",
  policy: "M6 3h9l3 3v15H6zM9 11h6M9 15h6",
  approval: "M5 12l4 4L19 6",
  remediation: "M14 6l4 4M3 21l6-6M16 3l5 5-12 12H4v-5z",
  report: "M4 19V9M10 19V5M16 19v-7M22 19H2",
};

function DeveloperNav({
  sessionId,
  runId,
  noSession,
  sessionsFailed,
}: {
  sessionId: string | undefined;
  runId: string | undefined;
  noSession: string;
  sessionsFailed: boolean;
}) {
  const sessionParams = sessionId ? { sessionId } : undefined;
  const runParams = runId ? { runId } : undefined;
  return (
    <>
      <Group label="WORKSPACE">
        <NavLink to="/developer/sessions" exact icon={<Icon d={icons.sessions} />}>
          Sessions
        </NavLink>
        <NavLink to="/setup" icon={<Icon d={icons.setup} />}>
          Connect editor
        </NavLink>
        <NavLink to="/developer/start" icon={<Icon d={icons.start} />}>
          Demo task runner
        </NavLink>
      </Group>
      <Group label={sessionId ? "CURRENT SESSION" : "SESSION"}>
        {sessionParams ? (
          <>
            <NavLink
              to="/developer/sessions/$sessionId"
              params={sessionParams}
              exact
              icon={<Icon d={icons.activity} />}
            >
              Activity
            </NavLink>
            <NavLink
              to="/developer/sessions/$sessionId/security"
              params={sessionParams}
              icon={<Icon d={icons.security} />}
            >
              Security
            </NavLink>
          </>
        ) : (
          <>
            <Unavailable icon={<Icon d={icons.activity} />} why={noSession}>
              Activity
            </Unavailable>
            <Unavailable icon={<Icon d={icons.security} />} why={noSession}>
              Security
            </Unavailable>
          </>
        )}
        {sessionsFailed ? (
          <p
            role="status"
            className="mx-3 mt-2 text-[11px] leading-snug text-rail-ink-faint"
          >
            Session navigation is unavailable.
          </p>
        ) : null}
      </Group>
      <Group label="GOVERNED EVIDENCE">
        {runParams ? (
          <NavLink to="/runs/$runId/memory" params={runParams} icon={<Icon d={icons.provenance} />}>
            HyperMesh memory
          </NavLink>
        ) : (
          <Unavailable icon={<Icon d={icons.provenance} />} why="The latest session has not projected a run yet">
            HyperMesh memory
          </Unavailable>
        )}
      </Group>
      <Group label="ACCOUNT">
        <NavLink to="/devices" icon={<Icon d={icons.devices} />}>
          Devices
        </NavLink>
      </Group>
    </>
  );
}

function AnalystNav() {
  return (
    <>
      <Group label="INVESTIGATION">
        <NavLink to="/analyst/queue" exact icon={<Icon d={icons.queue} />}>
          Priority queue
        </NavLink>
        <NavLink to="/analyst/queue" icon={<Icon d={icons.cases} />}>
          Investigations / cases
        </NavLink>
      </Group>
      <Group label="EVIDENCE">
        <NavLink to="/fleet/overview" icon={<Icon d={icons.fleet} />}>
          Fleet intelligence
        </NavLink>
        <NavLink to="/fleet" icon={<Icon d={icons.graph} />}>
          Evidence graph
        </NavLink>
        <NavLink to="/audit" icon={<Icon d={icons.audit} />}>
          Action log
        </NavLink>
      </Group>
      <Group label="ACCOUNT">
        <NavLink to="/devices" icon={<Icon d={icons.devices} />}>
          Devices
        </NavLink>
      </Group>
    </>
  );
}

function CisoNav() {
  return (
    <>
      <Group label="GOVERNANCE">
        <NavLink to="/ciso/overview" exact icon={<Icon d={icons.report} />}>
          Executive overview
        </NavLink>
        <NavLink to="/ciso/policies" icon={<Icon d={icons.policy} />}>
          Policies & exceptions
        </NavLink>
        <NavLink to="/ciso/approvals" icon={<Icon d={icons.approval} />}>
          Approvals
        </NavLink>
        <NavLink to="/ciso/remediation" icon={<Icon d={icons.remediation} />}>
          Remediation
        </NavLink>
        <NavLink to="/ciso/reports" icon={<Icon d={icons.report} />}>
          Reports
        </NavLink>
      </Group>
      <Group label="ASSURANCE">
        <NavLink to="/fleet/overview" icon={<Icon d={icons.fleet} />}>
          Fleet intelligence
        </NavLink>
        <NavLink to="/audit" icon={<Icon d={icons.audit} />}>
          Action log
        </NavLink>
        <NavLink to="/devices" icon={<Icon d={icons.devices} />}>
          Fleet devices
        </NavLink>
      </Group>
    </>
  );
}

export function Sidebar() {
  const { me, developer } = useIdentity();
  const sessions = useQuery({
    queryKey: ["developer-sessions"],
    queryFn: () => api.developerSessions(200),
    enabled: developer,
    refetchInterval: developer ? 5_000 : false,
  });
  const latestSession = sessions.data?.sessions[0];
  const noSession = sessions.isPending
    ? "Loading sessions…"
    : sessions.isError
      ? "Could not load sessions"
      : "No connected session yet";

  return (
    <aside className="flex max-h-[34vh] w-full shrink-0 flex-col overflow-auto bg-rail px-3.5 py-3 md:h-full md:max-h-none md:w-[252px] md:py-5">
      <Link
        to="/"
        className="flex items-center gap-3 rounded-lg px-2.5 pb-4 pt-1.5 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent-bright"
      >
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
      </Link>
      <nav aria-label="Main" className="flex flex-col md:min-w-[224px]">
        {me?.role === "developer" ? (
          <DeveloperNav
            sessionId={latestSession?.id}
            runId={latestSession?.run_id ?? undefined}
            noSession={noSession}
            sessionsFailed={sessions.isError}
          />
        ) : null}
        {me?.role === "analyst" ? <AnalystNav /> : null}
        {me?.role === "ciso" ? <CisoNav /> : null}
      </nav>
      <Identity me={me} />
    </aside>
  );
}

function initials(name: string): string {
  const parts = name
    .replace(/@.*$/, "")
    .split(/[.\s_-]+/)
    .filter(Boolean);
  return (
    parts
      .slice(0, 2)
      .map((part) => part[0])
      .join("") || name.slice(0, 2)
  ).toUpperCase();
}

function Identity({ me }: { me: Me | undefined }) {
  if (!me) return null;
  const labels = {
    developer: "Developer",
    analyst: "Security analyst",
    ciso: "CISO",
  } as const;
  const personas: Record<LocalRole, string> = {
    developer: "maya@company.com",
    analyst: "priya@company.com",
    ciso: "alex@company.com",
  };
  return (
    <div className="mt-auto border-t border-rail-line p-2.5">
      <div className="flex items-center gap-2.5">
        <div className="flex h-[30px] w-[30px] items-center justify-center rounded-full bg-rail-2 font-mono text-xs text-accent-bright">
          {initials(me.name)}
        </div>
        <div className="flex min-w-0 flex-col">
          <span className="truncate text-[13px] text-rail-ink-dim">
            {me.name}
          </span>
          <span className="text-[11px] text-rail-ink-faint">
            {labels[me.primary_role]}
          </span>
        </div>
      </div>
      {!me.verified ? (
        <>
          <p className="mt-2.5 rounded-md bg-rail-2 px-2.5 py-2 text-[11px] leading-snug text-rail-ink-faint">
            Local identity assertion. No identity provider verified this role.
          </p>
          <form
            className="mt-3 grid gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              const role = String(
                new FormData(event.currentTarget).get("local-role"),
              ) as LocalRole;
              setLocalIdentity(personas[role], role);
            }}
          >
            <label
              htmlFor="sidebar-local-role"
              className="font-mono text-[10px] tracking-widest text-rail-ink-faint"
            >
              TEST AS
            </label>
            <select
              id="sidebar-local-role"
              name="local-role"
              defaultValue={me.primary_role}
              className="w-full rounded-lg border border-rail-line bg-rail-2 px-2.5 py-2 text-xs text-rail-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent-bright"
            >
              <option value="developer">Developer · Maya</option>
              <option value="analyst">Analyst · Priya</option>
              <option value="ciso">CISO · Alex</option>
            </select>
            <button
              type="submit"
              className="w-full rounded-lg border border-rail-line px-3 py-2 text-left text-xs text-rail-ink-dim transition hover:bg-rail-hover focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-bright"
            >
              Switch local role
            </button>
          </form>
        </>
      ) : null}
      {oidcEnabled ? (
        <button
          type="button"
          onClick={signOut}
          className="mt-3 w-full rounded-lg border border-rail-line px-3 py-2 text-left text-xs text-rail-ink-dim transition hover:bg-rail-hover focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-bright"
        >
          Sign out
        </button>
      ) : null}
    </div>
  );
}
