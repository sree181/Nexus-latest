# Nexus UI/UX Redesign Direction

## Three candidate approaches

### 1. Theme Name: Civic Instrument Panel
**Very Brief Intro:** A contemporary public-safety command surface inspired by precision aviation instruments, municipal wayfinding, and editorial information design. It feels calm under pressure, authoritative, and visibly accountable rather than theatrical.
**Probability:** 0.07

### 2. Theme Name: Field Briefing Room
**Very Brief Intro:** A tactile, paper-and-pinboard operations room using warm map tones, stamped labels, layered notices, and physical briefing artifacts. It prioritizes human coordination and legibility over technological spectacle.
**Probability:** 0.03

### 3. Theme Name: Signal Ledger
**Very Brief Intro:** A stark monochrome systems ledger with oversized typography, numbered rails, and a single sharp signal color. It feels rigorous and audit-oriented, with a more experimental editorial rhythm.
**Probability:** 0.09

## Chosen Approach: Civic Instrument Panel

### Design Movement
The interface combines **International Typographic Style**, modern **transportation wayfinding**, and the restraint of **high-reliability control-room instrumentation**. It rejects sci-fi neon and generic dashboard cards in favor of a composed civic command environment.

### Core Principles
1. **Calm hierarchy under pressure:** Every view should answer “what needs attention, why, and what happens next” within seconds.
2. **Accountability is visible:** Operator identity, evidence state, decision status, and consequence language stay spatially connected to actions.
3. **Density without clutter:** Layering, spacing, rails, typographic contrast, and progressive disclosure organize information instead of simply placing more boxes.
4. **Operational truth over decoration:** Visual richness comes from maps, signal traces, status rhythms, and structured evidence—not ornamental gradients or faux analytics.

### Color Philosophy
The foundation uses near-black **midnight navy** rather than pure black to reduce fatigue and establish institutional confidence. Cool slate surfaces separate operational zones. A proprietary **Auburn Signal Orange** carries urgency, selection, and identity; it is used sparingly so that it remains meaningful. Mint indicates verified/live states, while red is reserved only for destructive or genuinely critical conditions. Off-white text is warmer than standard white to soften prolonged viewing.

### Layout Paradigm
Use an **asymmetric command frame**: a compact navigation rail, a flexible operational canvas, and a context/action rail that can collapse below the primary workspace on narrower screens. Within the canvas, use horizontal evidence bands and numbered decision stages rather than a uniform card grid. Sections should interlock through shared baselines, status rails, and subtle connector lines.

### Signature Elements
1. A segmented **signal rail** running along important regions to show severity, freshness, or workflow state.
2. **Indexed section markers** such as `01 / QUEUE`, `02 / EVIDENCE`, and `03 / DECISION` to make the process scannable and memorable.
3. A subtle **topographic contour texture** and low-contrast coordinate grid in the background, connecting the interface to place and movement without competing with data.

### Interaction Philosophy
Interactions should feel immediate, deliberate, and reversible. High-frequency actions receive restrained 120–180 ms feedback; major decision transitions use a composed 220–280 ms reveal. Hover states clarify hierarchy through contrast and a slight x-axis shift, while focus states remain highly visible. Destructive actions never rely on color alone.

### Animation
Use transform and opacity only. Queue items enter with a 40 ms stagger and a short upward drift. Selected incidents animate their signal rail and context content with a 220 ms cubic-bezier(0.23, 1, 0.32, 1) transition. Live indicators pulse subtly without glow. Buttons compress to 0.98 on press. All non-essential motion is disabled when `prefers-reduced-motion` is enabled.

### Typography System
Use **Archivo Variable** for identity, large display labels, and primary actions; **Source Sans 3 Variable** for dense operational copy and controls; and **JetBrains Mono Variable** for time, identifiers, hashes, telemetry, and audit metadata. Headlines use tightened tracking and sentence case; labels use moderate uppercase tracking only at small sizes. Avoid oversized novelty type that would impair operational scanning.

### Brand Essence
**Nexus Coordinate turns fragmented civic signals into accountable, human-approved action for command teams.** Personality: **composed, rigorous, decisive**.

### Brand Voice
Headlines state the operational truth directly. Calls to action name both the action and its consequence. Microcopy is concise, specific, and non-theatrical.

Example headline: **“Three decisions need an accountable owner.”**

Example CTA: **“Approve and record responsibility.”**

### Wordmark & Logo
The wordmark uses a custom wide Archivo treatment with the `X` formed as two crossing route vectors. The symbol is an abstract four-way coordination junction: four directional brackets converge around a protected center point. It must remain identifiable at favicon size without including text.

### Signature Brand Color
**Auburn Signal Orange — `oklch(0.74 0.17 55)`**. It is warmer and more civic than hazard yellow, unmistakable against midnight navy, and directly ties the product to Auburn without leaning on generic blue software branding.

## UI/UX Audit Summary

The current product contains strong operational content and careful responsibility language, but most presentation is encoded as large inline-style templates. The interface is locked to a 1920px design width through root font scaling, which weakens responsive behavior and makes smaller displays feel like reduced screenshots rather than thoughtfully recomposed workspaces. The dense three-column frame has limited progressive disclosure, several data regions share similar visual weight, and repeated dark rectangles create hierarchy primarily through borders rather than meaningful spatial rhythm.

The redesign will preserve all current handlers, permissions, decision validation, API behavior, evidence semantics, and audit copy. It will replace the fixed-scale presentation with responsive CSS, clarify the queue-to-evidence-to-decision journey, enrich the screen with restrained cartographic texture and status visualization, and improve keyboard focus, touch targets, empty states, and narrow-screen fallbacks.

## UX Improvement Matrix

| Current friction | Redesign response | Success signal |
| --- | --- | --- |
| The operator desk scales the root font from a fixed 1920px canvas, shrinking controls and text on common laptop widths. | Remove viewport-derived root sizing and introduce layout breakpoints at 1280px, 1024px, and 760px. | Core actions remain readable and usable without browser zoom from mobile through large command displays. |
| Queue, recommendation, evidence, and approval controls compete with nearly equal visual weight. | Establish a staged hierarchy: attention queue, decision brief, supporting proof, then accountable action. | A first-time operator can identify the current incident and primary action within a few seconds. |
| Repeated dark rectangles and thin borders make related and unrelated items look similar. | Use layered surface elevation, section indices, signal rails, and spacing rhythm to encode relationships. | Grouping remains clear even when color perception is reduced. |
| The decision rail exposes a high-consequence action in the same visual rhythm as supporting metadata. | Make the decision rail sticky on wide screens, clarify confirmation state, and visually bind evidence hash, identity, and action consequence. | The approval flow reads as one deliberate sequence rather than disconnected controls. |
| The command wall presents many panes but lacks a dominant spatial narrative. | Turn the wall into a place-first operational story: active incident, mobility field, staged workflow, desk readiness, and recent record. | The selected incident and present workflow stage dominate at a glance. |
| Status indicators rely heavily on small color dots and compact labels. | Pair color with iconography, text, shape, and explicit state language. | Live, degraded, dissenting, and blocked states remain distinguishable without color alone. |
| Sparse states feel like missing content rather than intentional readiness states. | Add purposeful empty-state composition, explanatory copy, and next-action affordances. | No-window and no-queue states explain why the area is empty and what the operator can do. |

## Responsive Composition

On large displays, the desk uses a **280px queue rail / fluid evidence canvas / 360px decision rail** structure. Between 1024px and 1279px, the decision rail remains visible but narrows and the evidence tables simplify. Below 1024px, the queue becomes a horizontal incident strip and the decision rail moves below the brief. Below 760px, the header condenses, the incident strip becomes scrollable, evidence rows become stacked summaries, and all action controls maintain at least 44px tap height.

The command wall remains widescreen-first, but it should no longer become illegibly scaled on ordinary desktops. Its navigation, KPI strip, incident brief, mobility artwork, agency desk matrix, and activity record will reflow rather than shrink. The wall may remain horizontally dense at true wall-display dimensions, but a standard 1440px preview must still preserve legible type and meaningful whitespace.

## Accessibility and Interaction Requirements

All interactive elements need visible `:focus-visible` treatment, native disabled semantics, and descriptive labels. Text must meet readable contrast against the actual rendered surface or image. Motion must respect `prefers-reduced-motion`. Active queue items, tabs, live states, and confirmation status must not depend on color alone. Button labels will describe consequences—especially approval, escalation, and decline—while existing server-side permission and validation behavior remains unchanged.

## Style Decisions

The desk remains the visual anchor for the system. Wall workspaces must each carry one dominant operational story, so evidence lineage and workflow are treated as full-scale command artifacts rather than small diagrams in an empty panel. Large Archivo and Source Sans statements describe current state and consequence; JetBrains Mono is reserved for timestamps, identifiers, hashes, and audit metadata. Numbered stages, signal rails, route connectors, and coordinate texture are structural wayfinding elements across all major screens. The Nexus wordmark gives its `X` the Auburn signal-orange route-vector emphasis so identity reads as part of the operating system rather than a generic product title.

The final implementation was validated at desktop and mobile breakpoints, built successfully for production, and passed the repository’s unit and command-wall interaction suites.

## Corrective Wall Composition

The deployed wall is current, but its primary geometry still follows the original design: a broad top metrics strip, a 38/62 incident-and-map split, a horizontal six-tab bar, a six-column desk strip, and a full-width audit record. Because those structural proportions were preserved, the redesign reads as a reskin even though the palette, surfaces, brand mark, evidence view, and workflow view changed.

The corrected operations wall will use a new **vertical route architecture**. A narrow left rail will carry the branded masthead, live mode, clock, and six numbered workspace controls. The active incident brief will become a focused upper-left placard; the map will occupy the dominant central field from top to bottom. KPI metrics will become a compact telemetry overlay across the map rather than a separate full-width band. Agency desks will form a two-column right-side accountability rail, and the 90-minute record will become a shallow bottom band aligned beneath the map and agency rail. This changes the silhouette, reading order, and interaction rhythm immediately while retaining all existing wall data, map behavior, desk configuration, and screen switching.

The new composition will use larger sentence-case operational statements, short placard labels, visible stage numbers, and route-like connector rules. It will avoid the prior pattern of many equally weighted horizontal rectangles. Evidence lineage and workflow will continue to occupy the full main canvas, but will inherit the same vertical route rail and branded masthead so every screen feels like one coherent civic command instrument.

## Map-First Hierarchy Refinement

The wall will be rebuilt as a **single geographic canvas with subordinate instruments**, not a collection of equal cards. The live map occupies the entire command field behind the interface. The incident brief becomes a focused translucent placard on the map’s upper-left edge, the six desks become a narrow accountability rail on the right, telemetry becomes a lightweight strip across the map’s upper center, and the record becomes a shallow lower-third band. Navigation remains a quiet vertical rail. This guarantees that geography is the first read, the active incident is the second, and agency state is the third.

Typography is restricted to three roles. **Archivo** is used only for the product wordmark, incident title, and section headlines. **Source Sans 3** is used for all explanatory and action copy. **JetBrains Mono** is reserved for identifiers, timestamps, counts, stage indices, and immutable record metadata. The wall uses a fixed six-step scale: micro metadata, labels, body, panel title, incident title, and hero status. Uppercase and tracking are limited to micro labels; all operational statements use sentence case. Weight, scale, and whitespace establish hierarchy before color or borders.

Supporting surfaces lose their equal-card treatment. Telemetry uses shared baselines and hairline dividers, desks become compact accountable rows, and the record is visually quiet until an event mark appears. Auburn orange is limited to active navigation, the current decision window, and evidence/record continuity. Green and red remain semantic states only. The map receives the highest luminance contrast and largest uninterrupted area on every operations viewport.
