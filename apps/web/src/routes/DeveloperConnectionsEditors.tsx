import { useMemo, useState } from "react";
import { Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";

import { DevIcon, type DevIconName } from "../components/DeveloperIcons";
import { useDeveloperProject } from "../components/DeveloperProject";
import { CopyButton, Disclosure, Status } from "../components/DeveloperVisual";
import { api } from "../lib/api";

type Editor = "cursor" | "claude";
type Step = "editor" | "device" | "privacy" | "hook" | "verify";

const steps: Array<{ id: Step; label: string; icon: DevIconName }> = [
  { id: "editor", label: "Editor", icon: "cursor" },
  { id: "device", label: "Device", icon: "device" },
  { id: "privacy", label: "Privacy", icon: "eye" },
  { id: "hook", label: "Hook", icon: "connect" },
  { id: "verify", label: "Verify", icon: "check" },
];

export function repositoryConfig(gate: boolean, excludes: string[]): string {
  return JSON.stringify({ record: true, gate, exclude: excludes }, null, 2);
}

export function editorHook(editor: Editor, checkout: string): string {
  const cursorCommand = `python3 ${checkout}/adapters/cursor/meshagent_hook.py`;
  const claudeCommand = `python3 ${checkout}/adapters/claude-code/meshagent_hook.py`;
  if (editor === "cursor") {
    return JSON.stringify({ version: 1, hooks: {
      beforeSubmitPrompt: [{ type: "command", command: cursorCommand, timeout: 5 }],
      afterFileEdit: [{ type: "command", command: cursorCommand, timeout: 5 }],
      beforeShellExecution: [{ type: "command", command: cursorCommand, timeout: 6, failClosed: false }],
      afterShellExecution: [{ type: "command", command: cursorCommand, timeout: 5 }],
      sessionEnd: [{ type: "command", command: cursorCommand, timeout: 10 }],
    } }, null, 2);
  }
  return JSON.stringify({ hooks: {
    UserPromptSubmit: [{ hooks: [{ type: "command", command: claudeCommand, timeout: 5 }] }],
    PreToolUse: [{ matcher: "Bash", hooks: [{ type: "command", command: claudeCommand, timeout: 6 }] }],
    PostToolUse: [{ matcher: "Write|Edit|MultiEdit|NotebookEdit|Bash", hooks: [{ type: "command", command: claudeCommand, timeout: 5 }] }],
    SessionEnd: [{ hooks: [{ type: "command", command: claudeCommand, timeout: 10 }] }],
  } }, null, 2);
}

function CodeBlock({ value }: { value: string }) {
  return <div className="dev-code-block"><span className="dev-code-copy"><CopyButton value={value} /></span>{value}</div>;
}

export function DeveloperConnectionsEditors() {
  const project = useDeveloperProject();
  const health = useQuery({ queryKey: ["health"], queryFn: api.health, retry: false });
  const devices = useQuery({ queryKey: ["devices"], queryFn: api.devices, refetchInterval: 5_000 });
  const [editor, setEditor] = useState<Editor>("cursor");
  const [step, setStep] = useState<Step>("editor");
  const [label, setLabel] = useState("Work laptop");
  const [apiUrl, setApiUrl] = useState(window.location.origin);
  const [checkout, setCheckout] = useState("");
  const [gate, setGate] = useState(true);
  const [excludes, setExcludes] = useState(["*.env", "secrets/*", "*.pem"]);
  const [newExclude, setNewExclude] = useState("");
  const root = checkout || health.data?.checkout || "/path/to/meshagent";
  const sessionsForEditor = project.allSessions.filter((session) => session.adapter === (editor === "claude" ? "claude-code" : "cursor"));
  const recorded = devices.data?.some((device) => device.last_used > 0) ?? false;
  const pairCommand = `MESHAGENT_API=${apiUrl} python3 ${root}/cli/meshagent.py login --label ${JSON.stringify(label)}`;
  const done = useMemo(() => new Set<Step>([
    "editor",
    ...(devices.data?.length ? ["device" as Step] : []),
    ...(sessionsForEditor.length ? ["privacy" as Step, "hook" as Step, "verify" as Step] : []),
  ]), [devices.data?.length, sessionsForEditor.length]);

  return (
    <div className="dev-scroll">
      <section className="dev-surface dev-setup">
        <nav className="dev-setup-steps" aria-label="Editor setup">
          {steps.map((item) => <button key={item.id} type="button" className={`dev-setup-step ${step === item.id ? "dev-setup-step-active" : ""} ${done.has(item.id) ? "dev-setup-step-done" : ""}`} aria-current={step === item.id ? "step" : undefined} onClick={() => setStep(item.id)}><DevIcon name={done.has(item.id) ? "check" : item.icon} size={17} /><span>{item.label}</span></button>)}
        </nav>
        <div className="dev-setup-body">
          {step === "editor" ? (
            <div className="dev-stack">
              <div className="dev-option-grid">
                <button type="button" className={`dev-option ${editor === "cursor" ? "dev-option-selected" : ""}`} onClick={() => setEditor("cursor")}><span className="dev-integration-logo"><DevIcon name="cursor" /></span><span className="dev-integration-copy"><strong>Cursor</strong><small>{project.allSessions.some((item) => item.adapter === "cursor") ? "Observed" : "Not observed"}</small></span></button>
                <button type="button" className={`dev-option ${editor === "claude" ? "dev-option-selected" : ""}`} onClick={() => setEditor("claude")}><span className="dev-integration-logo"><DevIcon name="terminal" /></span><span className="dev-integration-copy"><strong>Claude Code</strong><small>{project.allSessions.some((item) => item.adapter === "claude-code") ? "Observed" : "Not observed"}</small></span></button>
              </div>
              <div><button type="button" className="dev-action dev-action-primary" onClick={() => setStep("device")}>Continue <DevIcon name="arrow" size={16} /></button></div>
            </div>
          ) : null}

          {step === "device" ? (
            <div className="dev-stack">
              <div className="dev-form-grid"><div className="dev-field"><label htmlFor="connect-api">API</label><input id="connect-api" value={apiUrl} onChange={(event) => setApiUrl(event.target.value)} /></div><div className="dev-field"><label htmlFor="connect-label">Device</label><input id="connect-label" value={label} onChange={(event) => setLabel(event.target.value)} /></div></div>
              <CodeBlock value={pairCommand} />
              <div className="flex flex-wrap items-center gap-2"><Status label={devices.data?.length ? "Approved" : "Waiting"} tone={devices.data?.length ? "success" : "warning"} icon="device" /><Link to="/developer/connections/devices" className="dev-action">Open devices <DevIcon name="arrow" size={15} /></Link><button type="button" className="dev-action dev-action-primary" onClick={() => setStep("privacy")}>Continue</button></div>
            </div>
          ) : null}

          {step === "privacy" ? (
            <div className="dev-stack">
              <div className="dev-option-grid">
                <button type="button" className="dev-option dev-option-selected"><DevIcon name="eye" /><span className="dev-integration-copy"><strong>Record</strong><small>Repository opted in</small></span></button>
                <button type="button" className={`dev-option ${gate ? "dev-option-selected" : ""}`} onClick={() => setGate((value) => !value)}><DevIcon name="security" /><span className="dev-integration-copy"><strong>{gate ? "Protect" : "Observe"}</strong><small>Package mode</small></span></button>
              </div>
              <div className="dev-exclusion-list">{excludes.map((item) => <button type="button" className="dev-exclusion-chip" key={item} onClick={() => setExcludes((values) => values.filter((value) => value !== item))}><DevIcon name="eye" size={13} />{item}<DevIcon name="x" size={12} /></button>)}</div>
              <form className="flex gap-2" onSubmit={(event) => { event.preventDefault(); const value = newExclude.trim(); if (value && !excludes.includes(value)) setExcludes((items) => [...items, value]); setNewExclude(""); }}><div className="dev-field flex-1"><label htmlFor="new-exclusion">Exclude</label><input id="new-exclusion" value={newExclude} onChange={(event) => setNewExclude(event.target.value)} placeholder="private/*" /></div><button type="submit" className="dev-action self-end">Add</button></form>
              <Disclosure label=".meshagent.json" icon="code"><CodeBlock value={repositoryConfig(gate, excludes)} /></Disclosure>
              <div><button type="button" className="dev-action dev-action-primary" onClick={() => setStep("hook")}>Continue</button></div>
            </div>
          ) : null}

          {step === "hook" ? (
            <div className="dev-stack">
              <div className="dev-field"><label htmlFor="meshagent-root">MeshAgent path</label><input id="meshagent-root" value={root} onChange={(event) => setCheckout(event.target.value)} /></div>
              <span className="dev-mono dev-muted">{editor === "cursor" ? ".cursor/hooks.json" : ".claude/settings.json"}</span>
              <CodeBlock value={editorHook(editor, root)} />
              <Disclosure label="MCP · optional" icon="connect"><CodeBlock value={JSON.stringify({ mcpServers: { meshagent: { command: "python3", args: [`${root}/adapters/mcp/meshagent_mcp.py`], env: { MESHAGENT_API: apiUrl } } } }, null, 2)} /></Disclosure>
              <div><button type="button" className="dev-action dev-action-primary" onClick={() => setStep("verify")}>Verify</button></div>
            </div>
          ) : null}

          {step === "verify" ? (
            <ul className="dev-check-list">
              {[
                ["Device approved", Boolean(devices.data?.length)],
                ["Device used", recorded],
                ["Session received", sessionsForEditor.length > 0],
                ["Activity received", sessionsForEditor.some((session) => session.last_acked_sequence > 1)],
                ["Evidence created", sessionsForEditor.some((session) => Boolean(session.run_id))],
              ].map(([name, ready]) => <li className="dev-check-row" key={String(name)}><DevIcon name={ready ? "check" : "live"} className={ready ? "dev-tone-success" : "dev-tone-warning"} /><span>{name}</span><Status label={ready ? "Ready" : "Waiting"} tone={ready ? "success" : "warning"} /></li>)}
            </ul>
          ) : null}
        </div>
      </section>
    </div>
  );
}
