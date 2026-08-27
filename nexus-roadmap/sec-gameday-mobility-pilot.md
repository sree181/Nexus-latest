# Nexus Coordinate — SEC Game Day Mobility Pilot Blueprint

**Purpose:** Transform Nexus Coordinate’s SEC Game Day scenario from a simulation into a supervised, real-time, multi-agency mobility decision-support pilot for Auburn home football game days.

**Design rule:** The initial pilot is **live advisory and human-authorized**. Nexus may collect evidence, detect risk, coordinate recommendations, and create an audit-ready action plan. It does **not** dispatch police or fire units, change traffic-signal timing, close roads, publish public alerts, or control infrastructure without the authoritative agency system and a credentialed human approval.

## 1. Pilot Definition

The pilot’s operational question is simple:

> Can a shared real-time picture of traffic, parking, transit, safety, and emergency access enable Auburn-area agencies to identify and resolve a game-day mobility issue faster and with clearer cross-agency accountability?

The first live pilot should cover the primary mobility window from **T–4 hours before kickoff through T+2 hours after game completion**. Auburn’s football gameday transit service publishes the same four-hour pre-kickoff and two-hour post-game operating window, and notes that gameday bus arrivals are affected by live traffic conditions rather than a fixed timetable. [1]

The pilot should initially focus on three decision patterns:

| Decision pattern | Example Nexus recommendation | Human owner |
|---|---|---|
| **Arrival congestion** | Redirect inbound visitors toward an approved remote lot, increase wayfinding visibility, and flag impacted corridors. | Traffic operations supervisor with Athletics/Transit coordination |
| **Parking and shuttle imbalance** | Recommend shuttle staging or a passenger-information update when occupancy, queue, and vehicle-position evidence point to a growing imbalance. | Parking/Transit lead |
| **Emergency-access risk** | Recommend pre-clearing a designated route or changing field-posture priorities when a queue, incident, or closure threatens the agreed emergency corridor. | Public safety or incident command lead |

## 2. Operating Timeline

| Window | Live objective | Nexus behavior | Human decision |
|---|---|---|---|
| **T–72 to T–24 hours** | Prepare the event operating picture. | Imports the game plan, approved parking map, published transit routes, expected closures, rostered agency contacts, and route constraints. | Event owner confirms the plan, operating roles, and approval matrix. |
| **T–4 to T–1 hour** | Manage arrival demand. | Watches corridor speed, queue risk, parking/lot status, shuttle position, and public incident feeds. Surfaces an early-warning decision card only when evidence crosses an agreed threshold. | Traffic, parking, and transit owners approve or reject the recommended response. |
| **T–1 hour to kickoff** | Protect pedestrian and emergency access. | Prioritizes campus ingress, pedestrian density, stadium-adjacent congestion, and emergency-route availability. | Public-safety lead assigns or confirms the operational response. |
| **During game** | Maintain readiness, not visual noise. | Suppresses routine alerts; escalates only service degradation, safety events, weather, cyber disruption, or emergency-access risks. | Incident owner reviews and authorizes changes. |
| **T+0 to T+2 hours** | Coordinate the egress plan. | Tracks approved post-game traffic pattern, remote-lot demand, shuttle availability, route saturation, and emergency corridor health. | Traffic/Transit/Public Safety use one shared approval workflow for major changes. |
| **T+2 to T+24 hours** | Learn and improve. | Produces an after-action timeline: evidence, decisions, approvals, execution status, impact indicators, and open issues. | Steering group reviews results and updates the next-game playbook. |

## 3. First Data Inputs and Their Readiness

The first pilot should avoid treating a public website as a production interface. Every source below must be labeled in Nexus with its owner, refresh interval, reliability state, and authorization basis.

| Data input | Value to the pilot | Initial readiness | Production condition |
|---|---|---|---|
| **ALDOT traffic data** | Baseline corridor volumes and calibration context for ATLAS. ALDOT’s traffic-data portal includes Auburn and Lee County. [2] | Available for historical/baseline context. | Confirm permitted refresh, data semantics, and terms before relying on it for live operational thresholds. |
| **ALGO Traffic public information** | Road incidents, conditions, and camera context for Alabama roads. ALGO states that it provides live camera feeds and road updates. [3] | Useful for operator reference. | A direct feed or licensed API requires an ALDOT agreement; do not scrape or automate a public viewer. |
| **City of Auburn traffic counts** | City-level traffic-count context and local corridor understanding. The City’s Traffic Engineering Division collects and maintains traffic-count data and traffic infrastructure. [4] | Baseline/context data. | City partnership needed for timely signal health, detector, or operational traffic feeds. |
| **TomTom flow data** | Live speed and congestion indicators already used by the Nexus prototype. | Available under the existing account/license configuration. | Validate coverage, rate limit, retention, and use rights for operational deployment. |
| **Tiger Transit location and route context** | Core evidence for shuttle delay, bunching, and remote-lot connection decisions. Auburn identifies ETA Spot as a real-time bus-location tool and publishes specific game-day routes. [1] | Public map and operational context are visible. | Auburn Transportation Services must authorize a supported data integration or feed. |
| **Parking occupancy and arrival/exit counts** | Essential for AQUA to distinguish a full lot from a transit or wayfinding problem. | Not currently available to Nexus. | Requires Athletics/Parking systems agreement and a normalized occupancy feed. |
| **Public safety/CAD incident status** | Essential for a trusted emergency-access picture. | Not currently available to Nexus. | Requires an agency-approved, privacy-minimized incident-status integration; no individual case detail in the pilot. |
| **Weather and emergency alerts** | Supports severe-weather contingencies that affect traffic and crowd flow. | Public source possible. | Confirm the authoritative alert source, operational thresholds, and agency message owner. |

## 4. The Seven Agents During a SEC Game Day

| Agent | Live mission | Evidence it uses | Recommendation examples | Required human approval |
|---|---|---|---|---|
| **ATLAS** | Keep arrival and egress corridors moving while avoiding spillback into campus and neighborhood streets. | Traffic speed, travel time, incidents, approved route plans, traffic counts. | “Activate the pre-approved inbound alternate-route message for the I-85 approach.” | City/ALDOT-authorized traffic operations owner. |
| **SENTINEL** | Protect pedestrian movement and public-safety access near campus and the stadium. | Approved incident status, crowd/pedestrian observations, road closures, incident-command updates. | “Move the pedestrian crossing supervisor to the high-conflict intersection and preserve the ambulance ingress lane.” | Public safety lead. |
| **PHOENIX** | Maintain a viable emergency-response corridor. | Emergency-route status, closures, traffic queues, approved unit-availability status. | “Request confirmation that the emergency route is clear before directing overflow traffic across it.” | Fire/EMS or incident command lead. |
| **FORGE** | Identify mobility risks caused by infrastructure conditions. | Signal/asset health where approved, road-condition reports, planned work, barrier status. | “Escalate the failed signal cabinet as a high-priority operational constraint and use the approved manual-control playbook.” | Public works/traffic engineering lead. |
| **AQUA** | Balance parking, remote-lot, curb, and shuttle demand. | Approved lot occupancy, shuttle location, route service state, queue reports, ADA constraints. | “Increase visible routing to the approved remote lot; stage the next available shuttle at Duck Samford Park.” | Parking/Transit lead. |
| **NEXUS** | Reconcile conflicts and maintain the incident action plan. | Agent findings, policy rules, commitments, approvals, operational objectives. | “Traffic diversion improves flow but conflicts with emergency access; use the alternate staging plan instead.” | Incident commander or designated event coordination lead. |
| **ECHO** | Maintain reliable communications and prepare verified public-facing language. | Network/telecom status, approved message templates, source reliability, cyber/security alerts. | “Draft, but do not send, a traveler-information message: ‘Use the designated remote lot; shuttle service is active.’” | Authorized communications owner. |

## 5. Human Approval Charter

Every live recommendation must identify one accountable person and one permitted outcome.

| Recommendation type | Can Nexus do it automatically? | Required approver | Audit record |
|---|---|---|---|
| Detect a possible mobility issue | Yes, if based on an authorized source. | None until the threshold is exceeded. | Source, timestamp, confidence, rule/model version. |
| Create a cross-agency recommendation | Yes. | None until action is proposed. | Evidence set, affected agencies, assumptions, forecast. |
| Assign a coordination task | Only after authorization. | Agency duty lead or incident commander. | Assignee, due time, acknowledgement, completion evidence. |
| Draft a public message | Yes, as a draft only. | Authorized communications owner. | Final text, approver, publication channel, timestamp. |
| Submit a work order/request | Only after approval. | Owning agency lead. | Request ID, connector status, response/confirmation. |
| Change a traffic device, roadway state, dispatch state, or emergency order | No direct action in Stage 1. | Authoritative agency system and its credentialed operator. | External-system confirmation; Nexus records the reference only. |

### 5.1 Agency ownership and escalation rules

Nexus must never resolve a cross-agency disagreement invisibly. If agents recommend incompatible actions, the platform should state the trade-off and route it to the named command owner.

| Operational conflict | Default decision owner | Nexus behavior | Escalation trigger |
|---|---|---|---|
| Traffic throughput conflicts with emergency-route protection | Public-safety or incident-command lead | Shows both impacts, identifies the blocked emergency corridor, and suppresses any recommendation that would compromise the agreed life-safety rule. | Emergency corridor is not verified as available. |
| Parking demand conflicts with campus pedestrian safety | Public-safety lead with Parking/Transit input | Prioritizes pedestrian safety and offers approved remote-lot/shuttle alternatives. | Queue reaches a protected pedestrian crossing or egress zone. |
| Shuttle utilization conflicts with road-closure plan | Transit/Parking lead with Traffic Operations input | Shows route impact, bus position, and the next feasible staging point. | An approved route becomes unavailable or service continuity falls below the agreed level. |
| Infrastructure issue conflicts with the event traffic plan | Traffic Engineering/Public Works lead | Places the failed asset or roadway constraint directly on the operational map and updates every affected recommendation. | A signal, barrier, or road condition creates an immediate safety or capacity hazard. |
| Conflicting public messages | Authorized communications owner | Produces a single draft with sources and requested agency confirmations; does not publish. | Public messaging would affect emergency routing, closures, or safety instructions. |

## 6. Command-Center UX for the Pilot

The pilot replaces “simulation progress” with **live incident management**. The design must be clear at six feet on a large touch wall, but also work on an operator’s desktop.

### 6.1 Main live screen

| Zone | Content | Why it is operationally useful |
|---|---|---|
| **Live status rail** | `LIVE SEC GAME DAY`, kickoff countdown or game phase, data freshness, active command owner, incident count, system health. | Prevents confusion between a demonstration and a live operating event. |
| **Plain-language incident brief** | “What changed, why it matters, who owns it, and what decision is pending.” | A dean or incident commander understands the situation without decoding metrics. |
| **Operational map** | Actual closures, approved parking/remote-lot locations, shuttle positions where authorized, emergency corridors, incidents, and source freshness. | Puts geographic context ahead of agent branding. |
| **Decision queue** | Top three decisions, each with owner, recommendation, evidence, expected effect, constraints, expiration time, and approve/reject/escalate buttons. | Makes the screen a tool for human action rather than passive monitoring. |
| **Agency commitment rail** | Compact state machine for shared commitments: `requested → acknowledged → approved → executing → verified`. | Replaces transient animation with durable operational accountability. |
| **Evidence drawer** | Opens any decision to show source data, agent reasoning, policy constraints, and audit history. | Allows an expert to challenge or validate a recommendation without losing context. |

### 6.2 Touch requirements

All primary controls must be at least **64 px high** on the wall display, remain reachable with one touch, and provide instant visual confirmation. The map can be panned and zoomed, but no action should require a map gesture to be discovered. An agent tap opens the data ownership and current commitments for that domain; it must not become the only path to a decision.

### 6.3 Example live decision card

> **Arrival congestion — I-85 / approved remote-lot approach**  
> **What changed:** Travel speed fell below the agreed threshold for 8 minutes while the primary lot approaches capacity.  
> **Recommended action:** Activate the pre-approved remote-lot wayfinding plan and stage the next shuttle at the designated stop.  
> **Why it matters:** This protects the emergency corridor and reduces the likelihood that inbound traffic spills into campus access roads.  
> **Required approval:** Traffic Operations + Parking/Transit.  
> **Decision expires:** 10 minutes.  
> **Evidence:** live traffic flow, approved lot occupancy feed, shuttle location, current route constraints.

## 7. Pilot Success Measures

Baseline values must be established from prior comparable home games before setting improvement targets. The pilot should not claim a numeric benefit until that baseline and data quality are verified.

| Measure | Definition | Why it matters |
|---|---|---|
| **Alert-to-awareness time** | Time from authorized source signal to a verified human-visible incident card. | Tests whether Nexus shortens awareness time. |
| **Awareness-to-decision time** | Time from verified issue to an authorized decision. | Measures coordination efficiency without implying automatic control. |
| **Cross-agency acknowledgement rate** | Percentage of assigned tasks acknowledged within the agreed window. | Demonstrates whether the shared workflow improves accountability. |
| **Emergency-corridor availability** | Time and frequency that the agreed route remains verified as available. | Aligns the pilot with public-safety outcomes. |
| **Transit/parking service exception resolution** | Time from detected imbalance to verified mitigating action. | Measures AQUA’s operational usefulness. |
| **Data freshness and source availability** | Share of time each critical feed meets its agreed freshness threshold. | Prevents the platform from overclaiming certainty. |
| **Operator usefulness** | Post-event structured feedback from each participating agency. | Ensures the system earns continued agency use. |

## 8. First Implementation Slice

The first build should be deliberately narrow and usable for a supervised tabletop or a live shadow-mode event.

| Build item | Included in first slice | Deferred until partnership approval |
|---|---|---|
| **Live status model** | Event phase, live/training state, source freshness, data-quality flags. | Full EOC/CAD situation board. |
| **Map** | ALDOT/TomTom context, approved static game-day zones, current road/incident layer where permitted. | Proprietary cameras, restricted safety locations, or signal control. |
| **AQUA pilot panel** | Simulated or authorized parking occupancy plus real/approved shuttle location context. | Direct transit dispatch or schedule changes. |
| **Decision workflow** | Human approval, rejection, reassignment, expiration, and audit log. | Direct work-order and device-control connectors. |
| **After-action report** | Timeline, decisions, evidence, and operator notes. | Automated performance claims without validated baseline data. |

### 8.1 UX delivery sequence

| Release | Operator-visible change | Acceptance condition |
|---|---|---|
| **UX-1: Live status shell** | Replaces the simulation step counter with an explicit `LIVE / TRAINING / REPLAY` mode indicator, event phase, source-freshness badges, and named command owner. | An operator can tell in one glance whether the screen reflects live operations and whether any data feed is stale. |
| **UX-2: Incident and decision queue** | Adds a plain-language incident brief and decision cards with evidence, owner, expected effect, expiry, and approval controls. | A duty lead can approve, reject, or escalate a recommendation from the touch wall without opening a secondary screen. |
| **UX-3: Commitment rail** | Replaces transient coordination tiles with durable cross-agency commitments and states: requested, acknowledged, approved, executing, verified. | A command lead can see every unresolved dependency and its owner. |
| **UX-4: Evidence and audit drawer** | Opens details from any incident, task, or recommendation without losing map context. | Every decision shows its sources, timestamps, agent/model version, policy constraints, and approval history. |
| **UX-5: After-action workspace** | Provides an event timeline, decision log, outcomes, source gaps, and structured operator feedback. | The pilot team can run a post-event review without manually reconstructing events from screenshots or chat messages. |

## 9. Partnership Sequence

| Partner | Requested pilot contribution | What Nexus returns |
|---|---|---|
| **Auburn University Transportation Services** | Approved gameday route/stop data and a supported way to access shuttle position or service-status information. | A shared transit/parking status view, delay/queue insight, and after-action dashboard. |
| **Auburn Athletics / Gameday Operations** | Event schedule, approved parking map, operational contacts, and game-day plan updates. | A common executive view of parking, mobility, and visitor-flow risk. |
| **City of Auburn Traffic Engineering** | Approved local traffic context and an agreed traffic-operations liaison. | Evidence-based cross-agency recommendations and a post-event traffic coordination record. |
| **City of Auburn Public Safety / Fire / EMS** | Emergency-corridor rules, escalation contacts, and a privacy-minimized incident-status model. | A common operating picture that preserves command authority and emergency access. |
| **ALDOT / ALGO** | Clarification on allowable access to live road information and any partner API/data-sharing path. | A transparent, safety-oriented use case for regional mobility coordination. |

## 10. Decisions to Confirm Before Coding the Live Pilot

1. **Pilot mode:** Begin with a supervised **shadow-mode** event, in which Nexus sees available live feeds and makes recommendations but does not enter the official decision process; or begin directly with a formal human-approved advisory workflow.
2. **First participating agencies:** Confirm the initial group. A practical first set is University Transportation Services, Athletics/Gameday Operations, City Traffic Engineering, and a public-safety liaison.
3. **First data authorization:** Choose the first one or two approved live sources beyond TomTom/ALDOT context—preferably shuttle/service status and parking occupancy.
4. **First approval owner:** Name the single role that can accept or reject the initial mobility decision cards.
5. **Pilot event:** Choose one home football game with a workable preparation window and a post-event review commitment.

## References

[1] [Auburn University Transportation Services, “Tiger Transit Football Gameday Service.”](https://www.auburn.edu/administration/transit/gameday/football/index.php)  
[2] [Alabama Department of Transportation, “Alabama Traffic Data.”](https://aldotgis.dot.state.al.us/TDMPublic/)  
[3] [ALGO Traffic, “ALGO Traffic.”](https://algotraffic.com/)  
[4] [City of Auburn, “Traffic Engineering.”](https://www.auburnal.gov/engineering-services/traffic-engineering/)  
[5] [Auburn Tigers, “Gameday Parking & Tailgating.”](https://auburntigers.com/fb-gameday/parking-and-tailgating)  
[6] [NIST SP 800-82 Rev. 3, “Guide to Operational Technology Security.”](https://csrc.nist.gov/pubs/sp/800/82/r3/final)  
[7] [CISA, “Principles for the Secure Integration of Artificial Intelligence in Operational Technology.”](https://www.cisa.gov/resources-tools/resources/principles-secure-integration-artificial-intelligence-operational-technology)
