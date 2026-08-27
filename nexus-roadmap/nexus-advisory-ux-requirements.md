# Nexus Coordinate — Human-Approved Advisory Workflow: UI/UX Requirements

**Scope:** SEC Game Day mobility pilot first; reusable for severe weather, cyber incidents, and infrastructure events.  
**Audience:** Auburn University, City of Auburn, public safety, transportation, Athletics/Gameday Operations, and Harbert College of Business stakeholders.  
**Product position:** A real-time, multi-agency **decision-support and coordination** platform. Nexus detects, explains, recommends, records, and verifies. Authorized people retain operational control.

## 1. Product Experience Objective

Nexus must help a responsible leader answer four questions in under ten seconds:

1. **What has changed?**
2. **Why does it matter now?**
3. **What action is recommended and who has authority to decide?**
4. **What has already been acknowledged, approved, or completed?**

The interface must not read like a machine-learning experiment. Terms such as *Q-value*, *reward*, *exploration*, and *episode* belong in a protected technical evidence view, not in the primary live command experience. The live product speaks in operational language: **incident, evidence, recommendation, owner, approval, task, confirmation, outcome**.

> **Non-negotiable principle:** A recommendation is never an action. The interface must visually distinguish the AI’s proposal from the human authorization and from confirmation in the authoritative agency system.

## 2. Design Principles

| Principle | Product requirement |
|---|---|
| **Human authority is visible** | Every action card names the required approver, agency owner, permission level, and decision expiry. |
| **Evidence before confidence** | A recommendation shows its sources, timestamps, freshness, constraints, and uncertainty—not a standalone confidence percentage. |
| **Plain language first** | The primary sentence uses operational English. Technical model detail is available only through an evidence drawer. |
| **Incidents, not agents, are the unit of work** | The map and queue organize around a real incident or operational objective. Agents appear as domain co-pilots and contributors. |
| **Live is unmistakable** | `LIVE`, `TRAINING`, and `REPLAY` have persistent, high-contrast, non-interchangeable visual states. |
| **Motion conveys state change only** | Animation may confirm a new request, acknowledgement, or escalation; it must never obscure active hazards, decisions, or source quality. |
| **Progress is durable** | A task remains visible as `requested → acknowledged → approved → executing → verified`; it does not disappear after an animation. |
| **Large display and workstation parity** | A six-foot touch wall provides immediate status and approvals; desktop users have the same workflow with denser evidence views. |

## 3. Users, Roles, and Permissions

| Role | Main workspace | May view | May do | Must not do |
|---|---|---|---|---|
| **Executive observer** | Executive Overview | All approved status summaries, active incidents, operational effects, completed decisions | Filter, drill into a read-only explanation, export approved summaries | Approve, assign, modify source data, or access restricted detail |
| **Incident command lead** | Incident Command | All approved incident evidence and commitments for assigned event | Set priority, select command owner, approve/escalate designated decision classes, close incident | Directly control agency systems through Nexus in the pilot |
| **Traffic operations lead** | Mobility Operations | Traffic, road condition, signal-health status where authorized, traffic recommendations | Acknowledge/approve traffic-domain advisory decisions, assign traffic tasks | Approve public safety or transit action outside their charter |
| **Parking and transit lead** | Mobility Operations | Lot status, shuttle/service status, ADA constraints, transit recommendations | Approve parking/transit tasking, acknowledge execution, add field notes | Change an external schedule or dispatch command through Nexus unless separately authorized |
| **Public safety liaison** | Incident Command | Privacy-minimized incident status, emergency-route state, closures, safety recommendations | Prioritize life-safety constraints, approve safety-related tasks, escalate to command | Expose protected CAD detail to unauthorized roles |
| **Communications owner** | Decision Center | Approved source facts and message drafts | Approve/revise/publish through the authoritative channel; record confirmation | Publish automatically from a Nexus recommendation |
| **Agency operator** | Agency Operations | Tasks and evidence assigned to their agency | Acknowledge, update, and verify assigned work; request help | Approve a decision outside granted authority |
| **Platform administrator** | System Health | Data feed health, access logs, configuration | Manage access, source configuration, policies, and audit exports | Alter or delete completed operational audit records |

The initial pilot uses **named individuals and agency roles**, not generic “admin” access. Every approval is associated with a person, role, time, and the version of the recommendation they approved.

## 4. Information Architecture

| Workspace | Primary question | Default content | Primary action |
|---|---|---|---|
| **Event Readiness** | “Are we prepared for this game day?” | Event schedule, approved plan, active roles, source status, route/lot configuration, open readiness checks | Confirm readiness or assign an unresolved check |
| **Executive Overview** | “What matters now?” | Plain-language current status, top incidents, services at risk, decisions awaiting approval, verified outcomes | Open an incident summary |
| **Incident Command** | “What decision must be made next?” | Incident brief, decision queue, map, commitments, active owners, constraints, escalation path | Approve, reject, revise, or escalate a decision |
| **Mobility Operations** | “What must our team do?” | Agency-filtered map, assigned tasks, traffic/transit/parking evidence, accepted recommendation details | Acknowledge, update, verify, or request help |
| **Decision Center** | “What is waiting on human authority?” | Ranked decision cards, approver, expiry, impacts, evidence, policy rules | Approve, reject, request revision, or delegate |
| **After-Action Review** | “What happened and what improves next time?” | Timeline, task outcomes, source quality, decision times, operator notes, exportable audit record | Add review notes and approve lessons learned |
| **Training & Replay** | “How would the plan perform?” | Explicitly synthetic/historical scenario data, playback controls, lessons, model/evidence comparison | Start/replay a training exercise only |

## 5. Global Navigation and Persistent Status

The top status rail appears on every operational screen. It is never hidden by a modal or full-screen map.

| Element | Requirement | Acceptance criterion |
|---|---|---|
| **Operating mode** | Persistent `LIVE`, `TRAINING`, or `REPLAY` badge with unique color, icon, label, and explanatory tooltip. | A user can identify the mode from a screenshot without reading the page body. |
| **Event phase** | Shows preparation, arrival, ingress, in-game readiness, egress, or after-action state. | The phase updates from the approved event timeline, not simulation steps. |
| **Command owner** | Displays the current incident command lead and agency. | One tap opens owner, delegates, escalation contacts, and authority scope. |
| **Source health** | Displays healthy, delayed, unavailable, or unverified count. | Any decision using stale/unavailable data receives a visible caution state. |
| **Critical incidents** | Shows a count and severity summary. | A tap opens only critical and high-priority items. |
| **Clock and freshness** | Shows local event clock and last data update time. | The current time and the most stale critical source are always visible. |

## 6. Core Live Command-Center Screen

The live command screen uses a **decision-first asymmetric layout**. It may retain the satellite map, but the map is supporting evidence—not the only way to understand or operate the event.

| Screen zone | Contents | UX requirements |
|---|---|---|
| **A. Status rail** | Mode, event phase, command owner, source health, clock, active incident count | Fixed at top; text and contrast legible from the intended viewing distance. |
| **B. Plain-language situation brief** | One to three short sentences: current event condition, most important risk, named owner, next decision | Must be understandable by a non-technical executive without opening a panel. |
| **C. Decision queue** | Top-ranked pending decisions and active approvals | Sits in the visual priority zone; never below decorative agent content. |
| **D. Operational map** | Authorized traffic/route/parking/shuttle/incidents/closure layers, emergency corridor, and source freshness | Defaults to operational objects; agent icons are smaller supporting indicators, not large opaque markers. |
| **E. Agency commitment rail** | Requested, acknowledged, approved, executing, verified commitments | Durable and chronological; supports filtering by agency or incident. |
| **F. Evidence drawer** | Sources, timestamps, policy constraints, assumption notes, model/version metadata, audit history | Opens in place without obscuring the current decision queue. |

### 6.1 The situation brief

The brief is the executive and command-center “front door.” It has this fixed language structure:

> **What changed:** [observable condition]  
> **Why it matters:** [operational impact and timeframe]  
> **Recommended next move:** [human-authorized action]  
> **Decision owner:** [role/agency] by [expiry time]

Example:

> **What changed:** Inbound travel speed near the approved remote-lot approach has fallen for eight minutes while primary-lot capacity is constrained.  
> **Why it matters:** Continued spillback could limit the designated emergency-access route before kickoff.  
> **Recommended next move:** Activate the pre-approved remote-lot wayfinding plan and stage the next available shuttle.  
> **Decision owner:** Traffic Operations and Parking/Transit leads within 10 minutes.

## 7. Decision Card Requirements

The decision card is the central interaction object. Each card represents one bounded, human-authorized choice, not a generic notification.

| Card section | Required content | Interaction |
|---|---|---|
| **Priority and state** | Severity, decision state, expiry time, and source freshness | Tapping opens a state timeline; state cannot be changed by tapping the badge. |
| **Decision title** | Plain-language operational decision, not agent or model terminology | Title fits in two lines at wall-display scale. |
| **What changed** | Observable signal and timeframe | Opens source evidence and data-quality flags. |
| **Why it matters** | Specific safety, capacity, accessibility, or service consequence | Must contain no invented impact figure. |
| **Recommended action** | One clear action or one bounded choice set | Can be approved only if required evidence and approver conditions are met. |
| **Constraints** | Emergency-route rule, ADA constraint, route restriction, event policy, data limitation | Visible without opening the evidence drawer when material to the decision. |
| **Approvers** | Required role(s), current status, delegation rule | Names role first; names person second, subject to access policy. |
| **Expected effect** | Directional, evidence-based result with an uncertainty note where appropriate | Uses language such as “expected to reduce queue risk,” not unverified guaranteed outcomes. |
| **Audit context** | Recommendation version, created time, evidence version, model/agent contributors | Available in drawer and export. |

### 7.1 Decision states

| State | Visual treatment | Meaning | Allowed action |
|---|---|---|---|
| **Draft recommendation** | Neutral outline | Evidence is being assembled; not ready for approval | Review evidence; request refinement |
| **Awaiting acknowledgement** | Amber with named recipient | A participating agency must confirm receipt | Acknowledge or request clarification |
| **Awaiting approval** | High-contrast primary state | All evidence is ready; the authorized role must decide | Approve, reject, revise, delegate, escalate |
| **Approved** | Blue/green with approver and time | Human authority granted the bounded plan | Create/track authorized tasks |
| **Executing** | Blue with live task status | Agency action is in progress through normal systems | Update progress; flag blocker |
| **Verified** | Green with evidence link | Outcome has been confirmed by source or operator | Close or feed after-action review |
| **Expired / superseded** | Gray/red with reason | A new condition, missing approval, or event phase invalidated the decision | View replacement decision or re-open evidence |

The visual design must never use green for a merely recommended action. Green means **verified completion**, not “AI believes this is good.”

### 7.2 Command-center layout specification

The wall-display composition below is a layout requirement, not a literal wireframe. It preserves a stable orientation during stressful operations while allowing details to change in place.

| Region | Desktop / wall allocation | Default behavior | Priority rule |
|---|---|---|---|
| **Situation column** | 22–26% of usable width, left side | Shows the live incident brief, command owner, affected services, and top active commitment. | Never collapses below the visibility of the primary incident sentence and owner. |
| **Operational map** | 46–54% of usable width, center | Shows active constraints, operational objects, and selected decision geography. | Is subordinate to decision and life-safety information; no opaque agent panel may conceal a critical map object. |
| **Decision column** | 28–32% of usable width, right side | Shows the top three pending decisions and active task exceptions. | Pending approvals always sort above informational updates. |
| **Commitment rail** | Full width below main content | Shows cross-agency request/acknowledgement/approval/execution/verification flow. | Remains visible while an incident is selected; scrolling cannot hide an overdue commitment. |
| **Evidence drawer** | Overlaying 32–40% of width from the right or left, depending on target content | Opens alongside—not instead of—the decision or map. | Maintains the current decision card and top status rail in view. |

At a standard large wall-screen breakpoint, the first viewport must include the status rail, current incident brief, top priority decision card, map position for the active incident, and the latest commitment state. No scrolling is required to understand the next decision.

### 7.3 Decision Center layout

The dedicated Decision Center supports periods with multiple pending approvals. It uses a two-panel layout:

| Panel | Contents | User interaction |
|---|---|---|
| **Queue panel** | Sorted decision cards grouped by `life safety`, `event-critical`, `time-sensitive`, and `informational`. | A tap selects one card; filters include agency, severity, expiry, state, and source quality. |
| **Review panel** | Full decision summary, evidence, constraints, prior actions, approver chain, task preview, and audit history. | The approver can approve, reject, revise, delegate, or escalate using a persistent action bar. |

The action bar always displays the exact proposed outcome: for example, **“Approve advisory: request remote-lot shuttle staging; no signal control action.”** This prevents ambiguity about what a confirmation will do.

## 8. Approval, Rejection, Revision, and Escalation Flows

### 8.1 Approval flow

1. The operator opens a decision card and sees the summary, constraints, required approvers, expiry, and source health.
2. The operator selects **Review & Approve**; the confirmation sheet shows the exact action text, scope, expected effect, applicable policy, and tasks that will be created.
3. The operator confirms with their named role. For consequential decisions, Nexus requires a deliberate confirmation interaction—not a single accidental tap.
4. Nexus writes an immutable approval event, changes the card to **Approved**, and creates the bounded agency commitments.
5. The owning agency acknowledges work through Nexus or the connected authoritative system. Nexus never claims execution until it receives a human or system confirmation.

### 8.2 Rejection and revision flow

A rejection requires a structured reason chosen from a concise list plus optional free text: inadequate evidence, operationally infeasible, policy conflict, wrong owner, duplicate, timing no longer valid, or other. Nexus must preserve rejected recommendations in the audit record and prevent the same unmodified recommendation from reappearing without new evidence.

### 8.3 Escalation flow

An operator may choose **Escalate** when the action crosses agency authority, conflicts with life-safety rules, requires a second approving role, or is blocked. Escalation sends the same bounded decision—not a vague alert—to the next designated role with an explicit reason and expiry. The originating operator remains visible as the requestor.

### 8.4 Delegation flow

Delegation is role-aware. The delegator selects an eligible person/role, sets a delegation expiry, and sees whether the delegate has the required authority. The original owner remains in the audit trail and receives completion/expiry notices.

## 9. Agency Commitments and Execution Confirmation

Nexus should replace the existing decorative, rotating phase tiles with a **persistent commitment rail**. It is a simple shared state machine that keeps cross-agency coordination visible after the initial communication moment.

| Commitment state | Required visible fields | Example |
|---|---|---|
| **Requested** | Requesting agency, receiving agency, requested outcome, time, required response window | Traffic Operations requests Parking/Transit to confirm remote-lot shuttle staging. |
| **Acknowledged** | Recipient and acknowledgement time | Parking/Transit acknowledges the request. |
| **Approved** | Approval owner, approval time, approved scope | Incident lead approves the pre-approved wayfinding plan. |
| **Executing** | Assigned operator/team, last update, blocker indicator | Transit lead confirms first shuttle is being staged. |
| **Verified** | Verification source, time, observed result | ETA/service status and operator note confirm the shuttle is operating on the designated route. |

Every commitment has a visible **owner**, **deadline**, **last update**, and **evidence link**. A commitment can be visually emphasized with an animated transition only at the moment it first changes state.

## 10. Map and Agent Interaction Requirements

### 10.1 Map

The map must prioritize operational layers in this order:

1. Active incident locations and service disruption areas.
2. Emergency-access corridors and protected constraints.
3. Approved closures, direction changes, parking/remote-lot status, and transit routes.
4. Congestion, travel-time, weather, or infrastructure evidence.
5. Agents as compact service-domain indicators.

Every mapped item exposes: **what it is, source, last updated time, confidence/data quality, owner, affected decision/task, and a link to audit history**. Source freshness is visualized independently from incident severity; a stale source cannot look like a confirmed current condition.

### 10.2 Agents

The seven agents remain a useful educational and coordination metaphor, but they should change from oversized map objects into **agency-domain chips**. Selecting an agent opens its current responsibilities, evidence sources, active recommendations, blocked commitments, and contact/ownership—not a fictional backstory while in live mode.

The existing rich personas and MARL concepts remain available in **Training & Digital Twin** mode and an educational “How Nexus Works” panel.

## 11. Large Touch Display Requirements

The wall display is a shared situational-awareness and approval surface. It must be operable by a standing user without precise pointing.

| Requirement ID | Requirement | Acceptance test |
|---|---|---|
| **TOUCH-01** | All primary actions use a minimum 64 × 64 CSS-pixel hit target in wall-display mode; secondary actions use at least 48 × 48 pixels. | A user can activate every primary action with a finger without accidental neighboring activation. |
| **TOUCH-02** | The platform follows WCAG pointer-target guidance at minimum and exceeds it for wall-display primary controls. WCAG 2.2 sets a 24 × 24 CSS-pixel minimum at Level AA; Nexus’s 64-pixel primary target is an intentional operational standard. [1] | Automated and manual target-size checks pass at the wall layout breakpoint. |
| **TOUCH-03** | Approval, rejection, and stop/escalate controls use a confirmation sheet and cannot be triggered by map gestures. | A simulated accidental tap does not authorize a decision. |
| **TOUCH-04** | Primary decisions remain reachable without horizontal scrolling or a precision map gesture at 4K and ultrawide resolutions. | The top three priority cards and current command status are visible on first view. |
| **TOUCH-05** | Touch feedback appears in under 150 ms, with a clear pressed and selected state. | User testing confirms visible acknowledgement for every action. |
| **TOUCH-06** | Two-finger map manipulation and one-finger UI interaction do not conflict. | Map pan/zoom does not activate an adjacent decision control. |
| **TOUCH-07** | A persistent “Return to Command View” control exits any detail mode. | No user becomes trapped in a drawer, map view, or agent panel. |

## 12. Accessibility and Readability Requirements

| Requirement ID | Requirement |
|---|---|
| **A11Y-01** | Status uses color, text, iconography, and pattern—not color alone. Severity and decision state must remain distinguishable for color-vision differences. |
| **A11Y-02** | Core text meets WCAG contrast requirements; low-contrast decorative labels are prohibited in operational states. |
| **A11Y-03** | Wall-display typography uses a minimum 20 px body text, 26 px decision-card text, and 32 px primary situation headline at the base layout; it scales upward on 4K displays. |
| **A11Y-04** | Keyboard navigation, visible focus, and screen-reader labels are available on workstation layouts. |
| **A11Y-05** | All auto-advancing or flashing activity is nonessential, can be paused, and never hides urgent content. |
| **A11Y-06** | A plain-language summary is always available for each active incident and decision, independent of maps, charts, or agent terminology. |

## 13. Safety, Trust, and Audit Requirements

Nexus will eventually interact with transportation, communications, or other operational environments. NIST identifies safety, reliability, and performance as distinctive operational technology requirements, while CISA emphasizes secure AI integration and continuous validation. [2] [3] The UX must make those controls observable.

| Requirement ID | Requirement | Acceptance criterion |
|---|---|---|
| **SAFE-01** | No live action card presents a direct operational control in the Stage 1 pilot. | All cards end in a human task, approved request, or external-system reference—not a device command. |
| **SAFE-02** | Recommendations using stale, unavailable, or conflicting inputs display a caution banner and cannot be approved without an explicit override reason. | Stale-source test prevents silent approval. |
| **SAFE-03** | The system explains why a recommendation was generated, including sources, time window, constraints, and contributing agents. | Evidence drawer includes all required fields. |
| **SAFE-04** | Every approval, rejection, delegation, escalation, task update, and verification is immutable and exportable. | A completed incident produces a chronological audit export. |
| **SAFE-05** | Permission is enforced at the control level, not merely by hiding buttons. | Unauthorized API/UI attempts are denied and logged. |
| **SAFE-06** | The system has a visible data-freshness and system-health state. | An outage appears in the top rail and on affected cards within the agreed detection interval. |
| **SAFE-07** | Training/replay mode cannot send notifications, create work orders, or invoke execution connectors. | Mode-isolation test shows all live actions disabled in training/replay. |

## 14. Notifications and Attention Management

Nexus must reduce alert fatigue rather than create another stream of alarms.

| Alert type | Delivery | Interaction | Escalation rule |
|---|---|---|---|
| **Informational change** | Commitment rail / activity history | No interruption | Never escalates by itself |
| **Decision awaiting acknowledgement** | Agency workspace plus gentle top-rail count | Acknowledge or clarify | Escalates only after response window expires |
| **Decision awaiting approval** | Decision Center and assigned approver notification | Review, approve, reject, revise, delegate, escalate | Escalates according to role/expiry policy |
| **Life-safety or emergency-access conflict** | Persistent high-priority incident banner and assigned command role | Open/acknowledge/escalate | Cannot be dismissed without acknowledgement; never automatically executes an action |
| **Data quality issue** | Source-health rail and affected decision cards | Open health detail or acknowledge operational impact | Escalates if it invalidates a critical recommendation |

## 15. First Redesign Slice: Build Order

The first implementation should not attempt a complete interface replacement. It should make the advisory workflow tangible with the smallest coherent operational loop.

| Priority | Build | User value | Done when |
|---|---|---|---|
| **1** | Live/Training/Replay status rail and event phase model | Makes the environment’s operational truth explicit. | Every screen visibly states mode, event phase, command owner, clock, and source health. |
| **2** | Plain-language incident brief and decision-card system | Turns simulation narration into accountable human decisions. | A traffic/parking recommendation can be reviewed, approved/rejected/revised, and audited end to end. |
| **3** | Approval state machine and role permissions | Establishes human authority and trust. | Only assigned role(s) can approve; every decision has an immutable state history. |
| **4** | Commitment rail and agency task updates | Makes coordination durable and visible. | A cross-agency request survives beyond the initial animation and reaches verified/expired status. |
| **5** | Evidence drawer and source-freshness handling | Allows agency stakeholders to trust or challenge recommendations. | Every live card names sources, timestamps, constraints, and data-quality state. |
| **6** | Operational map hierarchy | Makes the map useful rather than decorative. | Incidents, emergency corridors, approved constraints, and decision-linked layers outrank agent icons. |

## 16. Prototype Scenarios for Stakeholder Review

The UX should be validated with three short, realistic walkthroughs before implementation is approved:

| Scenario | Decision to test | Stakeholders who should participate |
|---|---|---|
| **Arrival congestion and lot pressure** | Approve remote-lot wayfinding and shuttle staging recommendation while protecting the emergency route. | Traffic Operations, Transportation Services, Parking, Athletics/Gameday Operations, Public Safety liaison. |
| **Post-game egress conflict** | Resolve a conflict between the egress plan and emergency-access requirement. | Incident command, Traffic Operations, Fire/EMS/Public Safety, Communications. |
| **Critical data-source degradation** | Decide whether to hold, revise, or expire a recommendation when transit or traffic data is stale. | All operational owners plus platform administrator. |

## 17. Design Review Questions

Before implementation begins, the stakeholder group should answer the following:

1. Which roles may approve each of the initial three advisory decision types?
2. What counts as sufficient evidence to surface a high-priority recommendation?
3. Which operational constraints are non-negotiable, especially emergency access, ADA, public safety, and approved road closures?
4. What is the maximum acceptable age for each data source before Nexus must downgrade or suppress a recommendation?
5. Which notification channels are permitted during a game day?
6. What exact confirmation proves that an approved task has been executed?
7. Which parts of the wall display may be visible to executives, students, visitors, or public audiences, and which require authenticated agency access?

## 18. Pilot UX Acceptance Checklist

The first live advisory pilot should not be declared ready until each of the following checks passes in a supervised game-day rehearsal.

| Category | Acceptance check |
|---|---|
| **Mode safety** | Training, replay, and live modes are visibly distinct; training/replay cannot create live tasks, notifications, or execution requests. |
| **Decision clarity** | A non-technical reviewer can correctly state the condition, impact, recommended action, required approver, and expiry after viewing a decision card for 10 seconds. |
| **Approval safety** | The approver sees the bounded operational action, evidence, constraints, and task preview before confirmation; accidental single-tap approval is not possible. |
| **Role enforcement** | A user without authority cannot approve by changing URL, refreshing the page, or interacting with a hidden control. |
| **Source transparency** | Every active decision shows source name, last-updated time, and an explicit stale/unavailable state when applicable. |
| **Touch operation** | A standing user can open, review, approve/reject, and return to the command view using the wall display without a mouse or precision gesture. |
| **Accessibility** | Primary states are understandable without color; keyboard-only and screen-reader workflow pass for desktop use; display typography is readable at the intended viewing distance. |
| **Audit completeness** | The after-action export includes the incident timeline, evidence versions, recommendations, approvals/rejections, task updates, delegations, and verification notes. |
| **Operational continuity** | A delayed or unavailable noncritical source does not crash the command screen; affected recommendations are downgraded or blocked according to policy. |
| **Stakeholder confidence** | Representatives from the participating agencies can identify their authority boundary, task queue, and escalation path in the rehearsal. |

## References

[1] [W3C, “Understanding SC 2.5.8: Target Size (Minimum).”](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html)  
[2] [NIST SP 800-82 Rev. 3, “Guide to Operational Technology (OT) Security.”](https://csrc.nist.gov/pubs/sp/800/82/r3/final)  
[3] [CISA, “Principles for the Secure Integration of Artificial Intelligence in Operational Technology.”](https://www.cisa.gov/resources-tools/resources/principles-secure-integration-artificial-intelligence-operational-technology)
