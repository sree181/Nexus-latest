# Nexus Coordinate: From Simulation to a Real-Time Multi-Agency Operations Platform

**Prepared for:** Auburn University — Harbert College of Business  
**Prepared by:** Manus AI  
**Date:** August 25, 2026

## 1. Strategic Direction

Nexus should evolve into an **agency decision-support platform**, not a system that autonomously changes traffic signals, dispatches first responders, or controls infrastructure. Its early operational value comes from connecting live evidence, presenting a shared operating picture, coordinating recommendations across agencies, and documenting a human-authorized response.

> **Operating principle:** Nexus may detect, correlate, explain, and recommend. A credentialed human retains authority to approve, reject, or execute any action with real-world consequences.

The current simulation becomes a clearly labeled **Training & Digital Twin** mode. It remains valuable for rehearsals, tabletop exercises, policy testing, and learning agent behavior; it must never be visually confused with live operations.

## 2. The Future Operating Model

Every incoming event follows the same accountable lifecycle:

| Stage | What Nexus does | What the human does | Output |
|---|---|---|---|
| **Detect** | Ingests a data signal or agency alert, assesses freshness and confidence, and links it to location and service area. | Reviews only when an alert crosses the agreed threshold. | Verified incident signal |
| **Assess** | Combines traffic, weather, safety, infrastructure, transit, and cyber context into a shared operating picture. | Confirms incident priority and operational owner. | Situation summary with evidence |
| **Coordinate** | Proposes a cross-agency plan, identifies dependencies, and estimates impact and risk. | Assigns participants or adjusts the recommendation. | Coordinated action plan |
| **Authorize** | Enforces policy, role permissions, and required approvals. | Approves, rejects, escalates, or requests revision. | Auditable decision record |
| **Execute & Verify** | Sends an authorized work order or limited approved control request through an agency connector; measures the outcome. | Confirms field execution and closes or extends the incident. | Outcome record and after-action data |

Nexus should use a **read → recommend → approve → execute** maturity model. Direct machine control belongs only in the final stage, after agency agreements, security review, sandbox validation, operational policies, and human approval gates have been established.

## 3. The Seven Agents as Real-World Agency Co-Pilots

The existing seven agents should remain recognizable, but each becomes a focused co-pilot with explicit data contracts, decision rights, and escalation paths.

| Agent | Real-world agency role | Initial live inputs | What it may recommend | What it must not do without approval |
|---|---|---|---|---|
| **ATLAS** | Mobility and traffic operations co-pilot | ALDOT traffic counts, TomTom flow, ALGO incidents/cameras where licensed, city traffic counts, signal health | Diversions, signal timing plans, ramp/route messaging, queue-management priorities | Change a signal plan, close a road, or publish traffic control instructions |
| **SENTINEL** | Public safety and crowd-risk co-pilot | CAD-compatible incident feed, campus/city safety alerts, approved crowd or pedestrian telemetry, event plans | Perimeter recommendations, ingress/egress changes, crowd-risk escalation, mutual-aid requests | Dispatch police, issue a public-safety order, or access protected case details beyond policy |
| **PHOENIX** | Fire, EMS, and emergency-access co-pilot | CAD status feed, apparatus availability, route conditions, incident locations, hospital-status feeds where permitted | Emergency-route pre-clearance, staging recommendations, response-corridor protection | Dispatch an apparatus, alter clinical or triage decisions, or override incident command |
| **FORGE** | Public works and infrastructure co-pilot | Asset-management system, work orders, road-condition reports, sensor alarms, SCADA *read-only* telemetry | Crew prioritization, detour and repair sequencing, barrier or pump deployment recommendations | Operate a pump, utility, traffic cabinet, or other field device directly |
| **AQUA** | Transit, parking, and venue-flow co-pilot | Parking occupancy, shuttle GPS/AVL, event attendance projections, curb/lot availability | Remote-lot activation, shuttle frequency, staging locations, passenger messaging | Change an operator schedule or parking policy without agency authorization |
| **NEXUS** | Incident commander coordination co-pilot | Inputs from all agent domains, EOC status, incident objectives, approvals, and field updates | Conflict resolution, resource trade-offs, task sequencing, policy-aware incident action plans | Supersede an incident commander or authorize an agency action independently |
| **ECHO** | Communications and cyber-resilience co-pilot | Network health, SIEM alerts, approved public-alert systems, radio/telecom status, status-page feeds | Communication contingencies, verified public-message drafts, cyber containment recommendations | Send public alerts, issue media statements, isolate critical networks, or modify OT access |

The City of Auburn’s Traffic Engineering Division already monitors traffic volumes and maintains city traffic signals, signs, and markings; this makes it a natural partner for the **ATLAS** read-only pilot. [1] ALDOT’s traffic data portal includes Auburn and Lee County traffic data, while ALGO Traffic publishes live road updates and camera feeds; access terms and agency agreements should determine how those sources enter production Nexus. [2] [3]

For severe weather and emergency workflows, Nexus should complement—not replace—Auburn’s established emergency-preparedness channels, Lee County EMA, and Alabama EMA pathways. [4]

## 4. Production Architecture: Separate Agency Data From Agent Reasoning

The live platform should use a layered architecture, so no agent has unrestricted direct access to agency systems.

| Layer | Purpose | Required controls |
|---|---|---|
| **Agency adapters** | Connect each approved source: traffic, CAD, AVL, asset management, weather, parking, SIEM, or alerting. | Per-source credentials, read/write separation, schema validation, rate limits, health checks |
| **Event and evidence layer** | Stores normalized events, timestamps, source confidence, geographic context, and data lineage. | Immutable event IDs, retention policy, quality flags, replay capability |
| **Agent reasoning layer** | Lets agents assess an event, coordinate a recommendation, and explain assumptions. | Policy-constrained tool access, scenario boundaries, confidence thresholds, model/version tracking |
| **Decision and approval layer** | Presents recommendations, conflicts, approvals, and escalation rules. | Role-based access, dual approval when needed, expiration timers, complete audit trail |
| **Execution gateway** | Converts an approved action into a service request, work order, or tightly limited control command. | Allowlisted actions, least privilege, private connectivity, confirmation/rollback path, kill switch |
| **Operator experience** | Delivers a shared operating picture for executive, incident commander, dispatcher, and agency teams. | Accessibility, touch targets, distinct live/training states, event logging |

NIST identifies transportation systems, building automation, and physical-environment monitoring as operational technology (OT) and emphasizes their safety, reliability, and performance requirements. [5] CISA’s AI-in-OT guidance similarly advises continuous model monitoring, validation, and refinement, while its OT-connectivity guidance emphasizes intentionally designed, secured connectivity. [6] [7] These are the reasons Nexus should begin as a read-only, human-approved platform rather than autonomous control software.

## 5. UX Redesign: A Command Center for Real Work, Not a Simulation

The current map-first experience is effective for demonstrating agent collaboration. In live use, it needs to become **decision-first**: show what happened, what matters, who owns the next move, and what approval is needed.

### 5.1 Three purpose-built views

| Workspace | Primary user | Screen goal | Essential content |
|---|---|---|---|
| **Executive Overview** | Dean, mayor, EOC executive, senior leader | Understand the situation in 10 seconds. | City status, active incidents, services at risk, approved actions, outcome trend, plain-language summary |
| **Incident Command** | Incident commander, EOC lead, traffic/public-safety supervisor | Make and authorize coordinated decisions. | Incident timeline, agency commitments, proposed action plan, dependencies, impact estimate, approval queue |
| **Agency Operations** | Dispatcher, traffic engineer, public works, transit, IT/security operator | Work the assigned tasks. | Agency-filtered live feed, task queue, source evidence, field updates, SOP/checklist, handoff history |

Each workspace uses the same source of truth but presents a different level of detail. A dean sees a clear outcome narrative; an incident commander sees approvals and dependencies; a dispatcher sees only the tasks, evidence, and response window that matter to their desk.

### 5.2 Live screen layout

| Screen zone | Live operational purpose |
|---|---|
| **Top status rail** | Clearly states **LIVE** or **TRAINING**, incident severity, data freshness, active command owner, clock, and system-health state. |
| **Center operational map** | Geospatial incident context, route impact, assets, field units, hazards, and agency layers. Agents remain present but secondary to actual operational objects. |
| **Left incident narrative** | A concise, continuously updated plain-language account: what changed, why it matters, and what is expected in the next 15 minutes. |
| **Right decision queue** | The highest-value human choices first. Every card includes owner, evidence, expected impact, constraints, expiration time, and approve/reject/escalate controls. |
| **Bottom coordination rail** | A compact, live flow of cross-agency commitments. It replaces decorative rotating tiles with persistent status: *requested → acknowledged → approved → executing → verified*. |
| **Detail drawer** | Opens from any event, asset, route, or recommendation without hiding the operational map. It contains evidence, policy citations, participants, and full audit history. |

### 5.3 Interaction design rules

1. **No ambiguity about mode.** A persistent red/amber/blue badge distinguishes live operations, rehearsal, and replay.
2. **Every AI recommendation answers four questions.** “What happened?”, “What should we do?”, “Why is this safe or beneficial?”, and “Who must approve?”
3. **Motion has operational meaning.** Animated arcs are retained only for a newly acknowledged coordination request; they must not compete with active hazards or decision cards.
4. **Touch and desktop parity.** Primary controls use 64 px minimum touch targets on the wall display; keyboard-friendly equivalents support command-center workstations.
5. **Graceful degradation.** A source outage never becomes a false certainty. The UI must show the last-good timestamp, confidence, and affected recommendations.

### 5.4 The first 90 seconds of a live incident

The live experience should be tested against one simple question: can a supervisor understand and assign the response within 90 seconds without opening a simulation panel?

| Time | Operator sees | Operator action | Nexus response |
|---|---|---|---|
| **0–10 seconds** | A high-priority incident card with source, location, freshness, impacted service, and a one-sentence “why this matters.” | Opens the incident. | Centers the map and assembles evidence without interrupting other active incidents. |
| **10–30 seconds** | A plain-language situation brief and the affected agents/agencies. | Confirms severity and names the command owner. | Starts an auditable incident timeline and notifies only the relevant agency workspaces. |
| **30–60 seconds** | A ranked coordinated action plan with expected benefit, operational constraints, and required approvals. | Approves, revises, or escalates a recommended plan. | Converts the chosen plan into acknowledged cross-agency tasks. |
| **60–90 seconds** | A live execution board showing owner, status, evidence, and unresolved blockers. | Monitors completion or reassigns an overdue task. | Measures the outcome and prompts for closure or a follow-on decision. |

This replaces the current “simulation progress” framing with a **live incident lifecycle**. Agent personas remain valuable, but the operational unit of work becomes the incident, its evidence, its human owner, and its authorized tasks.

## 6. Real-World Data and Control Maturity

| Maturity stage | Nexus capability | Suitable data/control level | Example pilot outcome |
|---|---|---|---|
| **Stage 0 — Training** | Digital twin and scenario rehearsal | Synthetic, historical, and de-identified data | Demonstrate cross-agency coordination during SEC Game Day or tornado rehearsal |
| **Stage 1 — Live Advisory** | Real-time situational awareness and recommendations | Public data plus approved read-only agency feeds | Detect a traffic/weather conflict; create an auditable recommended action plan |
| **Stage 2 — Human-Authorized Workflows** | Tasking, work orders, and notification drafts | Agency integrations that create requests but do not directly control equipment | A supervisor approves a diversion plan and Nexus opens or updates the corresponding agency task |
| **Stage 3 — Limited Controlled Execution** | Allowlisted, reversible actions through agency gateways | Narrowly scoped controls with dual approval and continuous monitoring | Activate a pre-approved event signal plan or publish a pre-approved roadside message through the authoritative system |

The first viable product should target **Stage 1**, with a carefully selected Stage 2 workflow. The strongest initial proof of value is not a direct traffic-signal control; it is a shared real-time incident record, credible cross-agency recommendation, rapid human authorization, and measurable after-action result.

## 7. Delivery Options

Nexus needs a live, durable platform; simulation-style browser state alone is not sufficient. Two viable paths are below.

| Approach | Tradeoffs | Cost profile | Setup complexity |
|---|---|---|---|
| **A. Advisory pilot on the existing application foundation** | Fastest route to value. Uses public and approved read-only feeds, durable incident storage, a live event stream, role-based approvals, and human-created work orders. It deliberately avoids direct operational technology control. | Lower initial operational cost; agency data agreements and vendor access may still have costs. | Moderate. Suitable for a Harbert-led pilot with agency collaborators. |
| **B. High-assurance operational integration platform** | Supports private agency connectivity, dedicated integration gateways, formal identity federation, high availability, and tightly controlled execution. It is needed before direct signal, CAD, SCADA, or other sensitive actions. | Higher cost due to security review, private connectivity, vendor integrations, and ongoing operational support. | High. Requires agency sponsorship, legal/data-sharing agreements, security architecture review, and phased acceptance testing. |

The recommended path is **A first, then B only for the workflows that demonstrate value and receive agency sponsorship**. A pilot should run continuously, retain its events and approvals, and synchronize new evidence in near real time. The final deployment choice should be based on security, data residency, uptime, and procurement requirements—not just on the current simulation stack.

## 8. 12-Month Implementation Sequence

| Period | Outcome | Deliverables |
|---|---|---|
| **Months 0–2: Operating design** | Shared governance and first pilot definition | Agency charter, agent data contracts, read/write permission matrix, incident taxonomy, success metrics, UX prototype |
| **Months 2–4: Live advisory pilot** | A live incident picture for one use case | Data adapters for traffic/weather/event operations, evidence model, live decision queue, executive and incident-command views |
| **Months 4–6: Agency workflow pilot** | Human-authorized task coordination | Role-based approvals, handoff and task workflow, audit ledger, after-action reporting, reliability monitoring |
| **Months 6–9: Expand agent coverage** | Broader multi-agency value | Transit/parking, public works, emergency-access, and communications/cyber agency modules; training/replay library |
| **Months 9–12: Controlled-action assessment** | Decide whether any direct integration is justified | Security review, simulator/sandbox validation, approved action catalog, rollback and kill-switch testing, go/no-go decision |

### 8.1 Deployment decision matrix

| Capability | Advisory pilot | High-assurance agency operations |
|---|---|---|
| **Runtime** | A continuously available application service with a persistent event processor and managed database. The existing Railway deployment can be evaluated as the pilot baseline. | A managed enterprise environment selected jointly with agency IT/security teams, with documented availability, backup, recovery, and incident-response procedures. |
| **Data connectivity** | Public APIs and approved read-only agency feeds over authenticated HTTPS. | Segmented/private connections to agency systems through approved gateways. |
| **Identity** | Named user accounts, agency roles, multi-factor authentication, and full decision history. | Federated agency identity, least-privilege roles, privileged-access review, and recurring access certification. |
| **Execution** | Human-created work orders, notification drafts, and approved outbound links. | Allowlisted API commands with dual approvals, confirmation, rollback, and a tested kill switch. |
| **Availability target** | Suitable for demonstrations, exercises, and supervised pilot operations. | Defined service-level objective, monitoring, incident response, disaster recovery, and contractual vendor support. |

The pilot should prove that real-time coordination improves awareness and response quality **before** any investment in direct control integration. The high-assurance path should be initiated only for agency workflows that have a clear operational sponsor, a written data-sharing agreement, and a documented safety case.

## 9. First Decisions Needed From the Nexus Steering Group

The next design session should resolve five decisions before code is written:

| Decision | Why it matters |
|---|---|
| **Choose the first live use case** | Limits scope and lets Nexus demonstrate measurable value. Recommended candidates: SEC Game Day mobility, severe-weather coordination, or an infrastructure incident. |
| **Name the operational sponsor** | Defines who owns incident policy and who can authorize actions. |
| **Approve initial data sources** | Determines what can legally and safely be made live during Stage 1. |
| **Define the first approval workflow** | Keeps humans in command and makes the UI design concrete. |
| **Choose pilot success measures** | Examples: alert-to-awareness time, approval cycle time, task completion rate, and post-incident participant satisfaction. |

## References

[1] [City of Auburn, “Traffic Engineering.”](https://www.auburnal.gov/engineering-services/traffic-engineering/)  
[2] [Alabama Department of Transportation, “Alabama Traffic Data.”](https://aldotgis.dot.state.al.us/TDMPublic/)  
[3] [ALGO Traffic, “ALGO Traffic.”](https://algotraffic.com/)  
[4] [City of Auburn, “Emergency Preparedness.”](https://www.auburnal.gov/communications/emergency-prep/)  
[5] [NIST SP 800-82 Rev. 3, “Guide to Operational Technology (OT) Security.”](https://csrc.nist.gov/pubs/sp/800/82/r3/final)  
[6] [CISA, “Principles for the Secure Integration of Artificial Intelligence in Operational Technology.”](https://www.cisa.gov/resources-tools/resources/principles-secure-integration-artificial-intelligence-operational-technology)  
[7] [CISA, “Secure Connectivity Principles for Operational Technology (OT).”](https://www.cisa.gov/resources-tools/resources/secure-connectivity-principles-operational-technology-ot)
