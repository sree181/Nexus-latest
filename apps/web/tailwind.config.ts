import type { Config } from "tailwindcss";

/** Semantic names map onto the CSS variables in @meshagent/ui/tokens.css,
 *  so the palette lives in one place and both `bg-surface` and `var(--surface)`
 *  resolve to the same token. */
export default {
  content: [
    "./index.html",
    "./src/**/*.{ts,tsx}",
    "../../packages/ui/src/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        paper: "var(--paper)",
        surface: "var(--surface)",
        "surface-2": "var(--surface-2)",
        rail: "var(--rail)",
        "rail-2": "var(--rail-2)",
        "rail-ink": "var(--rail-ink)",
        "rail-ink-dim": "var(--rail-ink-dim)",
        "rail-ink-faint": "var(--rail-ink-faint)",
        "rail-hover": "var(--rail-hover)",
        "rail-line": "var(--rail-line)",
        ink: "var(--ink)",
        slate: "var(--slate)",
        "slate-2": "var(--slate-2)",
        "slate-3": "var(--slate-3)",
        line: "var(--line)",
        "line-2": "var(--line-2)",
        accent: "var(--accent)",
        "accent-bright": "var(--accent-bright)",
        "accent-soft": "var(--accent-soft)",
        risk: "var(--risk)",
        "risk-soft": "var(--risk-soft)",
        ok: "var(--ok)",
        "ok-soft": "var(--ok-soft)",
        warn: "var(--warn)",
        "warn-soft": "var(--warn-soft)",
        "plane-provenance": "var(--plane-provenance)",
        "plane-security": "var(--plane-security)",
        "plane-supply": "var(--plane-supply)",
        "plane-belief": "var(--plane-belief)",
      },
      fontFamily: {
        sans: ["IBM Plex Sans", "system-ui", "sans-serif"],
        serif: ["IBM Plex Serif", "Georgia", "serif"],
        mono: ["JetBrains Mono", "ui-monospace", "monospace"],
      },
    },
  },
  plugins: [],
} satisfies Config;
