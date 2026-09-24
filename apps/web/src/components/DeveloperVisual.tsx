import type { ButtonHTMLAttributes, ReactNode } from "react";
import { useEffect, useState } from "react";
import { Link, type LinkProps } from "@tanstack/react-router";

import { DevIcon, type DevIconName } from "./DeveloperIcons";

export type VisualTone = "neutral" | "accent" | "success" | "warning" | "danger" | "info";

const toneClass: Record<VisualTone, string> = {
  neutral: "dev-tone-neutral",
  accent: "dev-tone-accent",
  success: "dev-tone-success",
  warning: "dev-tone-warning",
  danger: "dev-tone-danger",
  info: "dev-tone-info",
};

export function Hint({
  label,
  children,
  side = "bottom",
}: {
  label: string;
  children: ReactNode;
  side?: "bottom" | "right" | "left";
}) {
  return (
    <span className="dev-hint group/hint">
      {children}
      <span role="tooltip" className={`dev-tooltip dev-tooltip-${side}`}>
        {label}
      </span>
    </span>
  );
}

export function IconButton({
  label,
  icon,
  selected = false,
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  label: string;
  icon: DevIconName;
  selected?: boolean;
}) {
  return (
    <Hint label={label}>
      <button
        type="button"
        aria-label={label}
        aria-pressed={selected || undefined}
        className={`dev-icon-button ${selected ? "dev-icon-button-selected" : ""} ${className}`}
        {...props}
      >
        <DevIcon name={icon} />
      </button>
    </Hint>
  );
}

export function IconLink({
  label,
  icon,
  to,
  params,
}: {
  label: string;
  icon: DevIconName;
  to: LinkProps["to"];
  params?: LinkProps["params"];
}) {
  return (
    <Hint label={label}>
      <Link to={to} params={params} aria-label={label} className="dev-icon-button">
        <DevIcon name={icon} />
      </Link>
    </Hint>
  );
}

export function Status({
  label,
  tone = "neutral",
  icon,
  title,
}: {
  label: string;
  tone?: VisualTone;
  icon?: DevIconName;
  title?: string;
}) {
  const value = (
    <span className={`dev-status ${toneClass[tone]}`} title={!title ? label : undefined}>
      {icon ? <DevIcon name={icon} size={14} /> : <span className="dev-status-dot" aria-hidden="true" />}
      <span>{label}</span>
    </span>
  );
  return title ? <Hint label={title}>{value}</Hint> : value;
}

export interface FlowNode {
  id: string;
  label: string;
  detail: string;
  icon: DevIconName;
  tone: VisualTone;
}

export function VisualFlow({ nodes, label }: { nodes: FlowNode[]; label: string }) {
  return (
    <ol className="dev-flow" aria-label={label}>
      {nodes.map((node, index) => (
        <li key={node.id} className="dev-flow-item">
          <Hint label={node.detail}>
            <span className={`dev-flow-node ${toneClass[node.tone]}`} tabIndex={0} aria-label={`${node.label}. ${node.detail}`}>
              <DevIcon name={node.icon} size={20} />
            </span>
          </Hint>
          <span className="dev-flow-label">{node.label}</span>
          {index < nodes.length - 1 ? <span className="dev-flow-line" aria-hidden="true" /> : null}
        </li>
      ))}
    </ol>
  );
}

export interface LocalTab {
  label: string;
  icon: DevIconName;
  to: LinkProps["to"];
  params?: LinkProps["params"];
  exact?: boolean;
}

export function IconTabs({ tabs, label }: { tabs: LocalTab[]; label: string }) {
  return (
    <nav aria-label={label} className="dev-tabs">
      {tabs.map((tab) => (
        <Hint key={tab.label} label={tab.label}>
          <Link
            to={tab.to}
            params={tab.params}
            aria-label={tab.label}
            activeOptions={{ exact: tab.exact ?? false }}
            className="dev-tab"
            activeProps={{ className: "dev-tab dev-tab-active", "aria-current": "page" }}
          >
            <DevIcon name={tab.icon} size={18} />
            <span>{tab.label}</span>
          </Link>
        </Hint>
      ))}
    </nav>
  );
}

export function SidePanel({
  title,
  icon,
  onClose,
  children,
  footer,
}: {
  title: string;
  icon: DevIconName;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
}) {
  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);

  return (
    <div className="dev-panel-layer" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose();
    }}>
      <aside className="dev-side-panel" role="dialog" aria-modal="true" aria-label={title}>
        <header className="dev-side-panel-header">
          <span className="dev-panel-title"><DevIcon name={icon} />{title}</span>
          <IconButton label="Close" icon="x" onClick={onClose} autoFocus />
        </header>
        <div className="dev-side-panel-body">{children}</div>
        {footer ? <footer className="dev-side-panel-footer">{footer}</footer> : null}
      </aside>
    </div>
  );
}

export function Disclosure({
  label,
  icon = "info",
  children,
}: {
  label: string;
  icon?: DevIconName;
  children: ReactNode;
}) {
  return (
    <details className="dev-disclosure">
      <summary><DevIcon name={icon} size={16} />{label}<DevIcon name="chevron" size={16} /></summary>
      <div className="dev-disclosure-body">{children}</div>
    </details>
  );
}

export function CopyButton({ value, label = "Copy" }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <IconButton
      label={copied ? "Copied" : label}
      icon={copied ? "check" : "copy"}
      onClick={() => {
        void navigator.clipboard?.writeText(value).then(
          () => {
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1_500);
          },
          () => setCopied(false),
        );
      }}
    />
  );
}

export function EmptyVisual({
  icon,
  title,
  action,
}: {
  icon: DevIconName;
  title: string;
  action?: ReactNode;
}) {
  return (
    <div className="dev-empty">
      <span className="dev-empty-icon"><DevIcon name={icon} size={24} /></span>
      <strong>{title}</strong>
      {action}
    </div>
  );
}
