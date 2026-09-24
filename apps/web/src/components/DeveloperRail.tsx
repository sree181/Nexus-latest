import { useState } from "react";
import { Link } from "@tanstack/react-router";

import { oidcEnabled, setLocalIdentity, signOut, type LocalRole } from "../lib/auth";
import { useIdentity } from "../lib/useIdentity";
import { DevIcon, type DevIconName } from "./DeveloperIcons";
import { Hint } from "./DeveloperVisual";

const personas: Record<LocalRole, string> = {
  developer: "maya@company.com",
  analyst: "priya@company.com",
  ciso: "alex@company.com",
};

function initials(name: string): string {
  return name.replace(/@.*$/, "").split(/[.\s_-]+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join("").toUpperCase() || "M";
}

function RailLink({ to, label, icon, exact = false, onClick }: {
  to: "/developer/sessions" | "/developer/attention" | "/developer/connections";
  label: string;
  icon: DevIconName;
  exact?: boolean;
  onClick?: () => void;
}) {
  return (
    <Hint label={label} side="right">
      <Link
        to={to}
        aria-label={label}
        onClick={onClick}
        className="dev-rail-link"
        activeProps={{ className: "dev-rail-link dev-rail-link-active", "aria-current": "page" }}
        activeOptions={{ exact }}
      >
        <DevIcon name={icon} />
        <span className="dev-rail-mobile-label">{label}</span>
      </Link>
    </Hint>
  );
}

function AccountPanel({ close }: { close: () => void }) {
  const { me } = useIdentity();
  if (!me) return null;
  return (
    <div className="dev-account-panel">
      <div className="dev-account-summary">
        <span className="dev-account-avatar">{initials(me.name)}</span>
        <span><strong>{me.name}</strong><small>{me.verified ? "Verified" : "Local identity"}</small></span>
      </div>
      {!me.verified ? (
        <form onSubmit={(event) => {
          event.preventDefault();
          const role = String(new FormData(event.currentTarget).get("role")) as LocalRole;
          setLocalIdentity(personas[role], role);
          close();
        }}>
          <label htmlFor="developer-role-switch">Test as</label>
          <select id="developer-role-switch" name="role" defaultValue={me.primary_role}>
            <option value="developer">Developer · Maya</option>
            <option value="analyst">Analyst · Priya</option>
            <option value="ciso">CISO · Alex</option>
          </select>
          <button type="submit">Switch</button>
        </form>
      ) : null}
      {oidcEnabled ? <button type="button" onClick={signOut}>Sign out</button> : null}
    </div>
  );
}

export function DeveloperRail() {
  const { me } = useIdentity();
  const [open, setOpen] = useState(false);
  const [account, setAccount] = useState(false);
  const [tools, setTools] = useState(false);
  const close = () => setOpen(false);

  return (
    <>
      <header className="dev-mobile-shell">
        <Link to="/developer/sessions" className="dev-mobile-brand" aria-label="MeshAgent Sessions">
          <span className="dev-brand-mark"><DevIcon name="evidence" size={18} /></span>
          <strong>MeshAgent</strong>
        </Link>
        <button type="button" className="dev-mobile-menu" aria-label="Open navigation" aria-expanded={open} onClick={() => setOpen(true)}>
          <DevIcon name="dots" />
        </button>
      </header>
      {open ? <button type="button" aria-label="Close navigation" className="dev-rail-overlay" onClick={close} /> : null}
      <aside className={`dev-rail ${open ? "dev-rail-open" : ""}`} aria-label="Developer navigation">
        <Link to="/developer/sessions" aria-label="MeshAgent" className="dev-rail-brand" onClick={close}>
          <DevIcon name="evidence" size={20} />
        </Link>
        <nav className="dev-rail-nav">
          <RailLink to="/developer/sessions" label="Sessions" icon="session" exact onClick={close} />
          <RailLink to="/developer/attention" label="Attention" icon="warning" onClick={close} />
          <RailLink to="/developer/connections" label="Connections" icon="connect" onClick={close} />
        </nav>
        {!me?.verified ? (
          <div className="dev-rail-tools">
            <Hint label="Development tools" side="right">
              <button type="button" className="dev-account-button" aria-label="Development tools" aria-expanded={tools} onClick={() => setTools((value) => !value)}>
                <DevIcon name="tool" />
                <span className="dev-rail-mobile-label">Development tools</span>
              </button>
            </Hint>
            {tools ? <div className="dev-account-panel"><Link to="/developer/start" className="dev-action w-full" onClick={() => { setTools(false); close(); }}>Demo Runner</Link></div> : null}
          </div>
        ) : null}
        <div className="dev-rail-account">
          <Hint label={me?.name ?? "Account"} side="right">
            <button type="button" className="dev-account-button" aria-label="Account" aria-expanded={account} onClick={() => setAccount((value) => !value)}>
              {me ? initials(me.name) : <DevIcon name="user" />}
              <span className="dev-rail-mobile-label">Account</span>
            </button>
          </Hint>
          {account ? <AccountPanel close={() => setAccount(false)} /> : null}
        </div>
      </aside>
    </>
  );
}
